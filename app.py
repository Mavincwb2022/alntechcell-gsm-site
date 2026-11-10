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
from flask import Flask, jsonify, render_template, request, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import select
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
_picpay_token_cache = {"value": "", "expires_at": 0.0}
_picpay_token_lock = threading.Lock()


def picpay_base_url():
    if os.getenv("PICPAY_SANDBOX", "false").lower() == "true":
        return "https://ecommerce-api.svcp.ppay.me"
    return "https://ecommerce-api.svcp.picpay.com"


def picpay_access_token():
    client_id = os.getenv("PICPAY_CLIENT_ID", "")
    client_secret = os.getenv("PICPAY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise ApiError("Configure PICPAY_CLIENT_ID e PICPAY_CLIENT_SECRET como secrets no Render.")
    with _picpay_token_lock:
        if _picpay_token_cache["value"] and time.monotonic() < _picpay_token_cache["expires_at"]:
            return _picpay_token_cache["value"]
        try:
            response = requests.post(
                f"{picpay_base_url()}/oauth2/token",
                json={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=(10, 25),
            )
            response.raise_for_status()
            result = response.json()
            token = result.get("access_token")
            if not token:
                raise ApiError("O PicPay não retornou um token de acesso.")
            _picpay_token_cache.update(value=token, expires_at=time.monotonic() + min(int(result.get("expires_in", 300)), 270))
            return token
        except (requests.RequestException, ValueError) as exc:
            raise ApiError("Não foi possível autenticar na API Pix PicPay.") from exc


def picpay_request(method, path, **kwargs):
    token = picpay_access_token()
    headers = kwargs.pop("headers", {})
    headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"})
    try:
        response = requests.request(method, f"{picpay_base_url()}{path}", headers=headers, timeout=(10, 30), **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}
    except (requests.RequestException, ValueError) as exc:
        app.logger.warning("Falha na API Pix PicPay (%s).", method)
        raise ApiError("O PicPay não concluiu a solicitação Pix. Tente novamente ou contate o suporte.") from exc


def picpay_phone(whatsapp):
    digits = re.sub(r"\D", "", whatsapp)
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    if len(digits) not in (10, 11):
        raise ApiError("Cadastre um WhatsApp brasileiro com DDD para pagar com Pix.")
    return {"countryCode": "55", "areaCode": digits[:2], "number": digits[2:], "type": "MOBILE"}


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
        return jsonify(get_catalog())
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


def confirmar_cobranca_picpay(payment):
    charge = picpay_request("GET", f"/charge/{payment.merchant_charge_id}")
    transactions = charge.get("transactions") or []
    paid = charge.get("chargeStatus") == "PAID" and any(
        tx.get("paymentType") == "PIX" and tx.get("transactionStatus") == "PAID"
        for tx in transactions
    )
    if not paid or int(charge.get("amount", -1)) != payment.valor_centavos:
        payment.status = str(charge.get("chargeStatus", "PENDING"))[:40]
        db.session.commit()
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
    if data.get("dados_picpay_ok") is not True:
        return jsonify({"erro": "Autorize o envio dos dados necessários ao PicPay para gerar o Pix."}), 400
    cpf = re.sub(r"\D", "", str(data.get("cpf", "")))
    if not re.fullmatch(r"\d{11}", cpf):
        return jsonify({"erro": "Informe um CPF válido para a cobrança Pix."}), 400
    try:
        catalog = get_catalog()
        service = next((s for s in catalog["services"] if s["id"] == str(data.get("servico_id", ""))), None)
        if not service:
            return jsonify({"erro": "Serviço não encontrado. Atualize o catálogo."}), 400
        rate = Decimal(os.getenv("PIX_CREDIT_RATE_BRL", "1"))
        price = Decimal(str(service.get("price", "")))
        amount = int((price * rate * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if amount < 1:
            return jsonify({"erro": "Este serviço não tem valor Pix válido. Escolha outro serviço ou fale com o suporte."}), 400
        phone = picpay_phone(user.whatsapp)
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
    payload = {
        "paymentSource": "GATEWAY",
        "merchantChargeId": charge_id,
        "customer": {
            "name": user.nome,
            "email": user.email,
            "documentType": "CPF",
            "document": cpf,
            "phone": phone,
        },
        "transactions": [{"amount": amount, "pix": {"expiration": 900}}],
    }
    try:
        result = picpay_request("POST", "/charge/pix", json=payload)
        transactions = result.get("transactions") or []
        pix = next((tx.get("pix", {}) for tx in transactions if tx.get("paymentType") == "PIX"), {})
        if not pix.get("qrCode"):
            raise ApiError("O PicPay não retornou o Pix Copia e Cola.")
        payment.picpay_id = result.get("id")
        payment.pix_copia_cola = pix.get("qrCode")
        payment.pix_qrcode_base64 = pix.get("qrCodeBase64")
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
        "pix_copia_cola": payment.pix_copia_cola,
        "pix_qrcode_base64": payment.pix_qrcode_base64,
    }), 201


@app.get("/api/pagamentos/<int:payment_id>")
def status_pagamento(payment_id):
    user = require_user()
    if not user:
        return jsonify({"erro": "Entre na sua conta para acompanhar o Pix."}), 401
    payment = db.session.get(PagamentoPix, payment_id)
    if not payment or (payment.usuario_id != user.id and user.papel != "admin"):
        return jsonify({"erro": "Pagamento não encontrado."}), 404
    if payment.status in ("PENDING", "PAID_CONFIRMED", "ORDER_ERROR"):
        try:
            confirmar_cobranca_picpay(payment)
        except ApiError as exc:
            app.logger.warning("Consulta Pix não disponível para pagamento %s: %s", payment.id, exc)
    return jsonify({"status": payment.status, "pedido_id": payment.pedido_id, "valor_centavos": payment.valor_centavos})


@app.post("/api/picpay/webhook")
def webhook_picpay():
    expected = os.getenv("PICPAY_WEBHOOK_TOKEN", "")
    supplied = request.headers.get("Authorization", "")
    if not expected or not hmac.compare_digest(supplied, expected):
        return jsonify({"erro": "Não autorizado."}), 401
    data = request.get_json(silent=True) or {}
    merchant_charge_id = (data.get("data") or {}).get("merchantChargeId")
    if not merchant_charge_id:
        return jsonify({"erro": "Cobrança não identificada."}), 400
    payment = db.session.scalar(select(PagamentoPix).where(PagamentoPix.merchant_charge_id == merchant_charge_id))
    if not payment:
        return jsonify({"erro": "Cobrança não encontrada."}), 404
    try:
        confirmar_cobranca_picpay(payment)
    except ApiError:
        return jsonify({"erro": "Não foi possível validar a cobrança no PicPay."}), 503
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
