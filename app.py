import base64
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hmac
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import wraps

import requests
from flask import Flask, jsonify, redirect, render_template, request, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, select, text
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("COOKIE_SECURE", "true").lower() == "true"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 12
database_url = os.getenv("DATABASE_URL", "sqlite:///local.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Keep the API access key in the hosting provider's secret environment settings.
UNLOCKCLEAN_API_KEY = os.getenv("UNLOCKCLEAN_API_KEY", "")
UNLOCKCLEAN_USERNAME = os.getenv("UNLOCKCLEAN_USERNAME", "alntechunlocker@gmail.com")
UNLOCKCLEAN_PUBLIC_URL = os.getenv(
    "UNLOCKCLEAN_PUBLIC_URL", "https://unlockingclean.com/public"
)


class Cliente(db.Model):
    __tablename__ = "clientes"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    whatsapp = db.Column(db.String(30), nullable=False, index=True)
    pedidos = db.relationship("Pedido", backref="cliente", lazy=True)


class Pedido(db.Model):
    __tablename__ = "pedidos"
    id = db.Column(db.Integer, primary_key=True)
    imei_sn = db.Column(db.String(80), nullable=False, index=True)
    modelo_aparelho = db.Column(db.String(100), nullable=False)
    servico_id = db.Column(db.Integer, nullable=False)
    servico_nome = db.Column(db.String(180), nullable=False)
    status = db.Column(db.String(40), nullable=False, default="Enviando")
    referencia_api = db.Column(db.String(80))
    resposta_api = db.Column(db.Text)
    data_criacao = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)


class Usuario(db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(254), nullable=False, unique=True, index=True)
    whatsapp = db.Column(db.String(30), nullable=False, unique=True, index=True)
    senha_hash = db.Column(db.String(255), nullable=False)
    papel = db.Column(db.String(20), nullable=False, default="usuario")
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    data_criacao = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class DonoPedido(db.Model):
    __tablename__ = "pedido_contas"
    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedidos.id"), nullable=False, unique=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False, index=True)


class PagamentoPix(db.Model):
    __tablename__ = "pagamentos_pix"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False, index=True)
    merchant_charge_id = db.Column(db.String(36), nullable=False, unique=True, index=True)
    picpay_id = db.Column(db.String(80))
    checkout_url = db.Column(db.Text)
    checkout_slug = db.Column(db.String(120))
    transaction_nsu = db.Column(db.String(120))
    status = db.Column(db.String(40), nullable=False, default="PENDING")
    valor_centavos = db.Column(db.Integer, nullable=False)
    servico_id = db.Column(db.Integer, nullable=False)
    servico_nome = db.Column(db.String(180), nullable=False)
    imei_sn = db.Column(db.String(80), nullable=False)
    modelo_aparelho = db.Column(db.String(100), nullable=False)
    campos_json = db.Column(db.Text, nullable=False, default="{}")
    pix_copia_cola = db.Column(db.Text)
    pix_qrcode_base64 = db.Column(db.Text)
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedidos.id"))
    data_criacao = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


with app.app_context():
    db.create_all()
    payment_columns = {column["name"] for column in inspect(db.engine).get_columns("pagamentos_pix")}
    for column_name, column_type in (("checkout_url", "TEXT"), ("checkout_slug", "VARCHAR(120)"), ("transaction_nsu", "VARCHAR(120)")):
        if column_name not in payment_columns:
            db.session.execute(text(f"ALTER TABLE pagamentos_pix ADD COLUMN {column_name} {column_type}"))
    db.session.commit()


class ApiError(Exception):
    pass


def call_unlockclean(action, parameters=None):
    if not UNLOCKCLEAN_USERNAME:
        raise ApiError("Configure UNLOCKCLEAN_USERNAME com o e-mail da sua conta antes de usar a API.")
    if not UNLOCKCLEAN_API_KEY:
        raise ApiError("Configure a chave da API como segredo UNLOCKCLEAN_API_KEY no Render.")

    from urllib.parse import urlparse

    parsed = urlparse(UNLOCKCLEAN_PUBLIC_URL)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ApiError("UNLOCKCLEAN_PUBLIC_URL precisa ser um endereço HTTPS válido.")
    endpoint = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}/api/index.php"
    encoded_parameters = (
        base64.b64encode(json.dumps(parameters, ensure_ascii=False).encode("utf-8")).decode("ascii")
        if parameters is not None
        else ""
    )
    try:
        response = requests.post(
            endpoint,
            data={
                "username": UNLOCKCLEAN_USERNAME,
                "apiaccesskey": UNLOCKCLEAN_API_KEY,
                "action": action,
                "requestformat": "JSON",
                "parameters": encoded_parameters,
            },
            headers={"User-Agent": "ALNTECH-CELL/1.0", "X-Powered-By": "GSM-THEME"},
            timeout=(15, 60),
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise ApiError("Não foi possível conectar à API UnlockCleaning.") from exc
    except ValueError as exc:
        raise ApiError("A API UnlockCleaning retornou uma resposta inválida.") from exc

    errors = payload.get("ERROR") if isinstance(payload, dict) else None
    if errors:
        message = errors[0].get("MESSAGE", "A API recusou a solicitação.") if errors else "A API recusou a solicitação."
        raise ApiError(str(message))
    return payload


def get_catalog():
    # The upstream API limits service-list refreshes to once every five minutes.
    with _catalog_lock:
        if _catalog_cache["value"] is not None and time.monotonic() - _catalog_cache["at"] < 240:
            return _catalog_cache["value"]
        payload = call_unlockclean("imeiservicelist")
        catalog = _parse_catalog(payload)
        _catalog_cache.update(at=time.monotonic(), value=catalog)
        return catalog


def _parse_catalog(payload):
    success = payload.get("SUCCESS", [])
    if not success:
        raise ApiError("A API não retornou o catálogo de serviços.")
    root = success[0]
    services = []
    for group in (root.get("LIST") or {}).values():
        for service in (group.get("SERVICES") or {}).values():
            services.append({
                "id": str(service.get("SERVICEID", "")),
                "name": service.get("SERVICENAME", "Serviço"),
                "group": group.get("GROUPNAME", "Serviços"),
                "type": service.get("SERVICETYPE", group.get("GROUPTYPE", "IMEI")),
                "price": service.get("CREDIT", ""),
                "time": service.get("TIME", ""),
                "custom": service.get("Requires.Custom", []),
                "custom_imei": service.get("CUSTOM", {}),
            })
    return {"services": services, "account": root.get("ACCOUNTINFO", {})}


_catalog_cache = {"at": 0.0, "value": None}
_catalog_lock = threading.Lock()
INFINITEPAY_API_URL = "https://api.checkout.infinitepay.io"


def pix_credit_rate():
    try:
        rate = Decimal(os.getenv("PIX_CREDIT_RATE_BRL", "1.70"))
    except InvalidOperation as exc:
        raise ApiError("PIX_CREDIT_RATE_BRL precisa ser um valor numérico positivo.") from exc
    if not rate.is_finite() or rate <= 0:
        raise ApiError("PIX_CREDIT_RATE_BRL precisa ser um valor numérico positivo.")
    return rate


def infinitepay_headers():
    api_key = os.getenv("INFINITEPAY_API_KEY", "").strip()
    if not api_key:
        raise ApiError("Configure INFINITEPAY_API_KEY como segredo no Render.")
    return {"X-Api-Key": api_key, "Accept": "application/json", "Content-Type": "application/json"}


def infinitepay_handle():
    handle = os.getenv("INFINITEPAY_HANDLE", "").strip().lstrip("$")
    if not handle:
        raise ApiError("Configure INFINITEPAY_HANDLE no Render com sua InfiniteTag, sem o $.")
    return handle


def infinitepay_request(path, payload):
    try:
        response = requests.post(
            f"{INFINITEPAY_API_URL}{path}", json=payload,
            headers=infinitepay_headers(), timeout=(10, 30),
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ApiError("A InfinitePay retornou uma resposta inválida.")
        return result
    except requests.RequestException as exc:
        app.logger.warning("Falha na API InfinitePay (%s).", path)
        raise ApiError("A InfinitePay não concluiu a solicitação. Tente novamente ou contate o suporte.") from exc
    except ValueError as exc:
        raise ApiError("A InfinitePay retornou uma resposta inválida.") from exc


@app.get("/")
def home():
    return render_template("index.html")


def current_user():
    user_id = session.get("user_id")
    return db.session.get(Usuario, user_id) if user_id else None


def require_user(admin=False):
    user = current_user()
    if not user or not user.ativo or (admin and user.papel != "admin"):
        return None
    return user


@app.get("/admin/setup")
def admin_setup_page():
    if not os.getenv("ADMIN_SETUP_TOKEN"):
        return "Configuração inicial desativada.", 404
    if db.session.scalar(select(Usuario.id).where(Usuario.papel == "admin")):
        return "A conta administradora já foi configurada.", 409
    return render_template("admin_setup.html", admin_email=os.getenv("ADMIN_EMAIL", "alntechunlocker@gmail.com"))


@app.post("/api/auth/register")
def registrar_usuario():
    data = request.get_json(silent=True) or {}
    nome = str(data.get("nome", "")).strip()[:100]
    email = str(data.get("email", "")).strip().lower()[:254]
    whatsapp = re.sub(r"[^0-9+]", "", str(data.get("whatsapp", "")))[:30]
    senha = str(data.get("senha", ""))
    if not nome or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(whatsapp) < 8:
        return jsonify({"erro": "Informe nome, e-mail válido e WhatsApp."}), 400
    if len(senha) < 12:
        return jsonify({"erro": "Use uma senha com pelo menos 12 caracteres."}), 400
    if db.session.scalar(select(Usuario.id).where((Usuario.email == email) | (Usuario.whatsapp == whatsapp))):
        return jsonify({"erro": "Já existe uma conta com esse e-mail ou WhatsApp."}), 409
    user = Usuario(nome=nome, email=email, whatsapp=whatsapp, senha_hash=generate_password_hash(senha))
    db.session.add(user)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"erro": "Não foi possível criar a conta. Verifique se o e-mail ou WhatsApp já está cadastrado."}), 409
    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    return jsonify({"usuario": {"nome": user.nome, "email": user.email, "whatsapp": user.whatsapp, "papel": user.papel}}), 201


@app.post("/api/auth/login")
def login_usuario():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    user = db.session.scalar(select(Usuario).where(Usuario.email == email))
    if not user or not user.ativo or not check_password_hash(user.senha_hash, str(data.get("senha", ""))):
        return jsonify({"erro": "E-mail ou senha inválidos."}), 401
    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    return jsonify({"usuario": {"nome": user.nome, "email": user.email, "whatsapp": user.whatsapp, "papel": user.papel}})


@app.post("/api/auth/logout")
def logout_usuario():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/auth/me")
def sessao_usuario():
    user = require_user()
    if not user:
        return jsonify({"usuario": None})
    return jsonify({"usuario": {"nome": user.nome, "email": user.email, "whatsapp": user.whatsapp, "papel": user.papel}})


@app.post("/api/auth/admin-setup")
def criar_conta_admin():
    expected_token = os.getenv("ADMIN_SETUP_TOKEN", "")
    admin_email = os.getenv("ADMIN_EMAIL", "alntechunlocker@gmail.com").strip().lower()
    data = request.get_json(silent=True) or {}
    token = str(data.get("token", ""))
    email = str(data.get("email", "")).strip().lower()
    senha = str(data.get("senha", ""))
    if not expected_token:
        return jsonify({"erro": "A configuração inicial está desativada."}), 404
    if db.session.scalar(select(Usuario.id).where(Usuario.papel == "admin")):
        return jsonify({"erro": "A conta administradora já foi configurada."}), 409
    if not hmac.compare_digest(token, expected_token) or email != admin_email:
        return jsonify({"erro": "Dados de configuração inválidos."}), 403
    if len(senha) < 12:
        return jsonify({"erro": "Use uma senha com pelo menos 12 caracteres."}), 400
    user = db.session.scalar(select(Usuario).where(Usuario.email == admin_email))
    if user:
        user.nome = user.nome or "Administrador"
        user.papel = "admin"
        user.senha_hash = generate_password_hash(senha)
    else:
        user = Usuario(nome="Administrador", email=admin_email, whatsapp="admin-" + secrets.token_hex(4), senha_hash=generate_password_hash(senha), papel="admin")
        db.session.add(user)
    db.session.commit()
    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    return jsonify({"ok": True})


@app.get("/admin")
def painel_admin():
    user = require_user(admin=True)
    if not user:
        return "Acesso restrito à administração.", 401
    return render_template("admin.html")


@app.get("/api/admin/resumo")
def resumo_admin():
    user = require_user(admin=True)
    if not user:
        return jsonify({"erro": "Acesso restrito à administração."}), 401
    users = db.session.scalars(select(Usuario).order_by(Usuario.data_criacao.desc())).all()
    orders = db.session.scalars(select(Pedido).order_by(Pedido.data_criacao.desc()).limit(250)).all()
    return jsonify({
        "usuarios": [{"nome": u.nome, "email": u.email, "whatsapp": u.whatsapp, "papel": u.papel, "ativo": u.ativo} for u in users],
        "pedidos": [{"id": p.id, "nome": p.cliente.nome, "whatsapp": p.cliente.whatsapp, "modelo": p.modelo_aparelho, "servico": p.servico_nome, "status": p.status, "criado": p.data_criacao.isoformat()} for p in orders],
    })


@app.get("/api/minha-conta/pedidos")
def meus_pedidos():
    user = require_user()
    if not user:
        return jsonify({"erro": "Entre na sua conta para ver os pedidos."}), 401
    pedidos = db.session.scalars(select(Pedido).join(DonoPedido, DonoPedido.pedido_id == Pedido.id).where(DonoPedido.usuario_id == user.id).order_by(Pedido.data_criacao.desc())).all()
    remote_ids = {p.id: {"ID": p.referencia_api} for p in pedidos if p.referencia_api}
    try:
        if remote_ids:
            payload = call_unlockclean("getimeiorderbulk", remote_ids)
            for pedido in pedidos:
                item = payload.get(str(pedido.id), {}).get("SUCCESS", [])
                if item:
                    status = int(item[0].get("STATUS", 0))
                    pedido.status = {4: "Concluído", 3: "Recusado", 1: "Em andamento", 0: "Aguardando"}.get(status, "Aguardando")
            db.session.commit()
    except ApiError:
        pass
    return jsonify({"pedidos": [{"id": p.id, "modelo": p.modelo_aparelho, "servico": p.servico_nome, "status": p.status, "criado": p.data_criacao.isoformat()} for p in pedidos]})


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/servicos")
def api_servicos():
    try:
        catalog = get_catalog()
        rate = pix_credit_rate()
        services = []
        for service in catalog["services"]:
            displayed = dict(service)
            try:
                price = Decimal(str(service.get("price", ""))) * rate
                displayed["price"] = format(
                    price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f"
                )
            except InvalidOperation:
                pass
            services.append(displayed)
        return jsonify({**catalog, "services": services})
    except ApiError as exc:
        return jsonify({"erro": str(exc)}), 502


def enviar_pedido_apos_pagamento(payment):
    if payment.pedido_id and payment.status == "PAID":
        return
    if payment.status == "PROCESSING":
        return
    user = db.session.get(Usuario, payment.usuario_id)
    if not user or not user.ativo:
        payment.status = "ORDER_ERROR"
        db.session.commit()
        return
    if payment.pedido_id:
        pedido = db.session.get(Pedido, payment.pedido_id)
    else:
        cliente = db.session.scalar(select(Cliente).where(Cliente.whatsapp == user.whatsapp))
        if cliente is None:
            cliente = Cliente(nome=user.nome[:100], whatsapp=user.whatsapp[:30])
            db.session.add(cliente)
            db.session.flush()
        pedido = Pedido(
            imei_sn=payment.imei_sn,
            modelo_aparelho=payment.modelo_aparelho,
            servico_id=payment.servico_id,
            servico_nome=payment.servico_nome[:180],
            status="Pagamento confirmado; enviando",
            cliente_id=cliente.id,
        )
        db.session.add(pedido)
        db.session.flush()
        db.session.add(DonoPedido(pedido_id=pedido.id, usuario_id=user.id))
        payment.pedido_id = pedido.id
    payment.status = "PROCESSING"
    db.session.commit()

    custom_fields = json.loads(payment.campos_json or "{}")
    parameters = {
        str(pedido.id): {
            "ID": int(payment.servico_id),
            "QNT": 1,
            "CUSTOMFIELD": base64.b64encode(json.dumps(custom_fields, ensure_ascii=False).encode("utf-8")).decode("ascii"),
        }
    }
    try:
        payload = call_unlockclean("placebulkorder", parameters)
        result = (payload.get("SUCCESS") or {}).get(str(pedido.id), {})
        if result.get("status") != "success":
            raise ApiError(result.get("message", "A API de serviços não confirmou o pedido."))
    except ApiError as exc:
        payment.status = "ORDER_ERROR"
        pedido.status = "Pagamento confirmado; envio aguardando suporte"
        pedido.resposta_api = str(exc)[:1000]
        db.session.commit()
        app.logger.error("Pedido pago %s precisa de reenvio manual.", pedido.id)
        return
    pedido.referencia_api = str(result.get("referenceid", "")) or None
    pedido.status = "Pendente"
    pedido.resposta_api = json.dumps(result, ensure_ascii=False)
    payment.status = "PAID"
    db.session.commit()


def confirmar_cobranca_infinitepay(payment):
    if not payment.checkout_slug or not payment.transaction_nsu:
        return False
    result = infinitepay_request("/payment_check", {
        "handle": infinitepay_handle(),
        "order_nsu": payment.merchant_charge_id,
        "transaction_nsu": payment.transaction_nsu,
        "slug": payment.checkout_slug,
    })
    if (result.get("success") is not True or result.get("paid") is not True
            or int(result.get("amount", -1)) != payment.valor_centavos):
        return False
    if payment.status not in ("PAID", "PROCESSING"):
        payment.status = "PAID_CONFIRMED"
        db.session.commit()
    enviar_pedido_apos_pagamento(payment)
    return payment.status == "PAID"


@app.post("/api/pagamentos/pix")
def criar_cobranca_pix():
    user = require_user()
    if not user:
        return jsonify({"erro": "Entre na sua conta para pagar com Pix."}), 401
    data = request.get_json(silent=True) or {}
    if data.get("autorizado") is not True:
        return jsonify({"erro": "Confirme que é proprietário do aparelho ou tem autorização expressa."}), 400
    try:
        handle = infinitepay_handle()
        infinitepay_headers()
        catalog = get_catalog()
        service = next((s for s in catalog["services"] if s["id"] == str(data.get("servico_id", ""))), None)
        if not service:
            return jsonify({"erro": "Serviço não encontrado. Atualize o catálogo."}), 400
        rate = pix_credit_rate()
        price = Decimal(str(service.get("price", "")))
        amount = int((price * rate * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if amount < 100:
            return jsonify({"erro": "Este serviço não tem valor Pix válido. Escolha outro serviço ou fale com o suporte."}), 400
    except (ApiError, InvalidOperation, ValueError) as exc:
        return jsonify({"erro": str(exc)}), 400
    charge_id = str(uuid.uuid4())
    payment = PagamentoPix(
        usuario_id=user.id,
        merchant_charge_id=charge_id,
        status="CREATING",
        valor_centavos=amount,
        servico_id=int(service["id"]),
        servico_nome=service["name"][:180],
        imei_sn=str(data.get("imei_sn", "")).strip()[:80],
        modelo_aparelho=str(data.get("modelo", "")).strip()[:100],
        campos_json=json.dumps(data.get("campos") if isinstance(data.get("campos"), dict) else {}, ensure_ascii=False),
    )
    if not payment.imei_sn or not payment.modelo_aparelho:
        return jsonify({"erro": "Informe modelo e IMEI/número de série."}), 400
    db.session.add(payment)
    db.session.commit()
    from datetime import timedelta
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    site_url = os.getenv("PUBLIC_BASE_URL", "https://alntechcell-gsm-site.onrender.com").rstrip("/")
    payload = {
        "handle": handle,
        "items": [{"description": payment.servico_nome, "price": amount, "quantity": 1}],
        "order_nsu": charge_id,
        "redirect_url": f"{site_url}/checkout/retorno",
        "webhook_url": f"{site_url}/api/infinitepay/webhook",
        "customer": {"name": user.nome, "email": user.email, "phone_number": user.whatsapp},
        "options": {"single_payment": True, "valid_until": expires_at},
    }
    try:
        result = infinitepay_request("/links", payload)
        checkout_url = result.get("url") or result.get("checkout_url")
        if not checkout_url or not str(checkout_url).startswith("https://"):
            raise ApiError("A InfinitePay não retornou um link de checkout válido.")
        payment.checkout_url = checkout_url
        payment.status = "PENDING"
        db.session.commit()
    except ApiError as exc:
        payment.status = "ERROR"
        db.session.commit()
        return jsonify({"erro": str(exc)}), 502
    return jsonify({
        "pagamento_id": payment.id,
        "status": payment.status,
        "valor_centavos": payment.valor_centavos,
        "checkout_url": payment.checkout_url,
    }), 201


@app.get("/api/pagamentos/<int:payment_id>")
def status_pagamento(payment_id):
    user = require_user()
    if not user:
        return jsonify({"erro": "Entre na sua conta para acompanhar o pagamento."}), 401
    payment = db.session.get(PagamentoPix, payment_id)
    if not payment or (payment.usuario_id != user.id and user.papel != "admin"):
        return jsonify({"erro": "Pagamento não encontrado."}), 404
    if payment.status == "PENDING" and payment.checkout_slug and payment.transaction_nsu:
        try:
            confirmar_cobranca_infinitepay(payment)
        except ApiError as exc:
            app.logger.warning("Consulta InfinitePay não disponível para pagamento %s: %s", payment.id, exc)
    return jsonify({"status": payment.status, "pedido_id": payment.pedido_id, "valor_centavos": payment.valor_centavos})


@app.get("/checkout/retorno")
def retorno_checkout():
    order_nsu = request.args.get("order_nsu", "")
    payment = db.session.scalar(select(PagamentoPix).where(PagamentoPix.merchant_charge_id == order_nsu))
    if payment:
        payment.checkout_slug = request.args.get("slug", "")[:120] or payment.checkout_slug
        payment.transaction_nsu = request.args.get("transaction_nsu", "")[:120] or payment.transaction_nsu
        db.session.commit()
        try:
            confirmar_cobranca_infinitepay(payment)
        except ApiError:
            app.logger.warning("Retorno da InfinitePay aguardando confirmação do pagamento %s.", payment.id)
        return redirect(f"/?pagamento_id={payment.id}")
    return redirect("/")


@app.post("/api/infinitepay/webhook")
def webhook_infinitepay():
    data = request.get_json(silent=True) or {}
    order_nsu = str(data.get("order_nsu", ""))
    if not order_nsu:
        return jsonify({"erro": "Cobrança não identificada."}), 400
    payment = db.session.scalar(select(PagamentoPix).where(PagamentoPix.merchant_charge_id == order_nsu))
    if not payment:
        return jsonify({"erro": "Cobrança não encontrada."}), 404
    payment.checkout_slug = str(data.get("invoice_slug", ""))[:120] or payment.checkout_slug
    payment.transaction_nsu = str(data.get("transaction_nsu", ""))[:120] or payment.transaction_nsu
    db.session.commit()
    try:
        confirmar_cobranca_infinitepay(payment)
    except ApiError:
        return jsonify({"erro": "Não foi possível validar a cobrança na InfinitePay."}), 503
    return jsonify({"ok": True})


@app.post("/api/pedidos")
def criar_pedido():
    return jsonify({"erro": "Os pedidos agora são enviados após a confirmação do Pix. Use a opção 'Gerar Pix' no catálogo."}), 410


@app.post("/api/pedidos/consultar")
def consultar_pedidos():
    data = request.get_json(silent=True) or {}
    user = require_user()
    if not user:
        return jsonify({"mensagem": "Entre na sua conta para consultar pedidos."}), 401
    imei_sn = str(data.get("imei_sn", "")).strip()
    if not imei_sn:
        return jsonify({"mensagem": "Informe o IMEI/serial do aparelho."}), 400

    pedidos = db.session.scalars(
        select(Pedido).join(DonoPedido, DonoPedido.pedido_id == Pedido.id).where(
            Pedido.imei_sn == imei_sn, DonoPedido.usuario_id == user.id
        ).order_by(Pedido.data_criacao.desc())
    ).all()
    if not pedidos:
        return jsonify({"mensagem": "Nenhum pedido encontrado com esses dados."}), 404

    remote_ids = {p.id: {"ID": p.referencia_api} for p in pedidos if p.referencia_api}
    try:
        if remote_ids:
            payload = call_unlockclean("getimeiorderbulk", remote_ids)
            for pedido in pedidos:
                item = payload.get(str(pedido.id), {}).get("SUCCESS", [])
                if item:
                    status = int(item[0].get("STATUS", 0))
                    pedido.status = {4: "Concluído", 3: "Recusado", 1: "Em andamento", 0: "Aguardando"}.get(status, "Aguardando")
            db.session.commit()
    except ApiError as exc:
        app.logger.warning("Consulta remota indisponível: %s", exc)

    return jsonify({"pedidos": [
        {"id": p.id, "modelo": p.modelo_aparelho, "servico": p.servico_nome, "status": p.status}
        for p in pedidos
    ]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
