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
    tipo_servico = db.Column(db.String(40), nullable=False, default="")
    valor_centavos = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(40), nullable=False, default="Enviando")
    quantidade = db.Column(db.Integer, nullable=False, default=1)
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
    tipo = db.Column(db.String(30), nullable=False, default="servico")
    valor_centavos = db.Column(db.Integer, nullable=False)
    quantidade = db.Column(db.Integer, nullable=False, default=1)
    servico_id = db.Column(db.Integer, nullable=False)
    servico_nome = db.Column(db.String(180), nullable=False)
    tipo_servico = db.Column(db.String(40), nullable=False, default="")
    imei_sn = db.Column(db.String(80), nullable=False)
    modelo_aparelho = db.Column(db.String(100), nullable=False)
    campos_json = db.Column(db.Text, nullable=False, default="{}")
    pix_copia_cola = db.Column(db.Text)
    pix_qrcode_base64 = db.Column(db.Text)
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedidos.id"))
    data_criacao = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class Carteira(db.Model):
    __tablename__ = "carteiras"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False, unique=True, index=True)
    saldo_centavos = db.Column(db.Integer, nullable=False, default=0)
    total_recebido_centavos = db.Column(db.Integer, nullable=False, default=0)
    data_atualizacao = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class LancamentoCarteira(db.Model):
    __tablename__ = "lancamentos_carteira"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False, index=True)
    referencia = db.Column(db.String(160), nullable=False, unique=True, index=True)
    tipo = db.Column(db.String(30), nullable=False)
    valor_centavos = db.Column(db.Integer, nullable=False)
    descricao = db.Column(db.String(200), nullable=False)
    data_criacao = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


with app.app_context():
    db.create_all()
    order_columns = {column["name"] for column in inspect(db.engine).get_columns("pedidos")}
    for column_name, column_type in (("quantidade", "INTEGER NOT NULL DEFAULT 1"), ("tipo_servico", "VARCHAR(40) NOT NULL DEFAULT ''"), ("valor_centavos", "INTEGER NOT NULL DEFAULT 0")):
        if column_name not in order_columns:
            db.session.execute(text(f"ALTER TABLE pedidos ADD COLUMN {column_name} {column_type}"))
    payment_columns = {column["name"] for column in inspect(db.engine).get_columns("pagamentos_pix")}
    for column_name, column_type in (("checkout_url", "TEXT"), ("checkout_slug", "VARCHAR(120)"), ("transaction_nsu", "VARCHAR(120)"), ("tipo", "VARCHAR(30) NOT NULL DEFAULT 'servico'"), ("quantidade", "INTEGER NOT NULL DEFAULT 1"), ("tipo_servico", "VARCHAR(40) NOT NULL DEFAULT ''")):
        if column_name not in payment_columns:
            db.session.execute(text(f"ALTER TABLE pagamentos_pix ADD COLUMN {column_name} {column_type}"))
    for user_id in db.session.scalars(select(Usuario.id)):
        wallet = db.session.scalar(select(Carteira.id).where(Carteira.usuario_id == user_id))
        if wallet is None:
            db.session.add(Carteira(usuario_id=user_id))
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


def get_wallet(user_id, lock=False):
    query = select(Carteira).where(Carteira.usuario_id == user_id)
    if lock:
        query = query.with_for_update()
    wallet = db.session.scalar(query)
    if wallet is None:
        wallet = Carteira(usuario_id=user_id)
        db.session.add(wallet)
        db.session.flush()
    return wallet


def wallet_summary(user):
    wallet = get_wallet(user.id)
    orders = db.session.scalars(
        select(Pedido).join(DonoPedido, DonoPedido.pedido_id == Pedido.id)
        .where(DonoPedido.usuario_id == user.id)
    ).all()
    statuses = [str(order.status or "").casefold() for order in orders]
    completed = {order.id for order, status in zip(orders, statuses) if "conclu" in status or "sucesso" in status or "devolvido" in status}
    blocked_cents = sum(order.valor_centavos for order in orders if order.id not in completed and order.valor_centavos > 0)
    return {
        "saldo_centavos": wallet.saldo_centavos,
        "bloqueado_centavos": blocked_cents,
        "total_recebido_centavos": wallet.total_recebido_centavos,
        "pedidos": {
            "aguardando": sum("pendente" in s or "aguardando" in s for s in statuses),
            "processando": sum("andamento" in s or "análise" in s or "enviando" in s for s in statuses),
            "sucesso": sum("concluído" in s or "concluido" in s or "sucesso" in s for s in statuses),
            "rejeitado": sum("recusado" in s or "rejeitado" in s for s in statuses),
            "total": len(orders),
        },
    }


def lancar_saldo(user_id, amount_cents, kind, reference, description, received=False):
    wallet = get_wallet(user_id, lock=True)
    if db.session.scalar(select(LancamentoCarteira.id).where(LancamentoCarteira.referencia == reference)):
        return False
    if wallet.saldo_centavos + amount_cents < 0:
        return None
    wallet.saldo_centavos += amount_cents
    if received and amount_cents > 0:
        wallet.total_recebido_centavos += amount_cents
    wallet.data_atualizacao = datetime.now(timezone.utc)
    db.session.add(LancamentoCarteira(
        usuario_id=user_id,
        referencia=reference,
        tipo=kind,
        valor_centavos=amount_cents,
        descricao=description[:200],
    ))
    db.session.commit()
    return True


@app.get("/api/carteira")
def resumo_carteira():
    user = require_user()
    if not user:
        return jsonify({"erro": "Entre na sua conta para ver sua carteira."}), 401
    return jsonify(wallet_summary(user))


@app.get("/api/carteira/lancamentos")
def lancamentos_carteira():
    user = require_user()
    if not user:
        return jsonify({"erro": "Entre na sua conta para ver sua carteira."}), 401
    entries = db.session.scalars(
        select(LancamentoCarteira).where(LancamentoCarteira.usuario_id == user.id)
        .order_by(LancamentoCarteira.data_criacao.desc()).limit(50)
    ).all()
    return jsonify({"lancamentos": [{
        "tipo": row.tipo,
        "valor_centavos": row.valor_centavos,
        "descricao": row.descricao,
        "criado": row.data_criacao.isoformat(),
    } for row in entries]})


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
        db.session.flush()
        db.session.add(Carteira(usuario_id=user.id))
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
    get_wallet(user.id)
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


@app.get("/minha-conta/pedidos")
def pagina_historico_pedidos():
    if not require_user():
        return redirect("/?entrar=1")
    return render_template("orders.html")


@app.post("/api/auth/profile/name")
def atualizar_nome_perfil():
    user = require_user()
    if not user:
        return jsonify({"erro": "Entre na sua conta para alterar o nome."}), 401
    data = request.get_json(silent=True) or {}
    nome = str(data.get("nome", "")).strip()[:100]
    if not nome:
        return jsonify({"erro": "Informe um nome válido."}), 400
    user.nome = nome
    for cliente in db.session.scalars(select(Cliente).where(Cliente.whatsapp == user.whatsapp)):
        cliente.nome = nome
    db.session.commit()
    return jsonify({"usuario": {"nome": user.nome, "email": user.email}})


@app.get("/api/admin/resumo")
def resumo_admin():
    user = require_user(admin=True)
    if not user:
        return jsonify({"erro": "Acesso restrito à administração."}), 401
    users = db.session.scalars(select(Usuario).order_by(Usuario.data_criacao.desc())).all()
    orders = db.session.scalars(select(Pedido).order_by(Pedido.data_criacao.desc()).limit(250)).all()
    return jsonify({
        "usuarios": [{"id": u.id, "nome": u.nome, "email": u.email, "whatsapp": u.whatsapp, "saldo_centavos": get_wallet(u.id).saldo_centavos, "papel": u.papel, "ativo": u.ativo} for u in users],
        "pedidos": [{"id": p.id, "nome": p.cliente.nome, "whatsapp": p.cliente.whatsapp, "tipo": p.tipo_servico or "—", "quantidade": p.quantidade, "valor_centavos": p.valor_centavos, "servico": p.servico_nome, "status": p.status, "criado": p.data_criacao.isoformat()} for p in orders],
    })


@app.post("/api/admin/pedidos/<int:order_id>/estornar")
def estornar_pedido_admin(order_id):
    admin = require_user(admin=True)
    if not admin:
        return jsonify({"erro": "Acesso restrito à administração."}), 401
    order = db.session.get(Pedido, order_id)
    if not order:
        return jsonify({"erro": "Pedido não encontrado."}), 404
    if order.status not in ("Em análise", "Recusado", "Rejeitado"):
        return jsonify({"erro": "O estorno manual só está disponível para pedidos em análise ou recusados."}), 409
    owner = db.session.scalar(select(DonoPedido).where(DonoPedido.pedido_id == order.id))
    debit = db.session.scalar(select(LancamentoCarteira).where(LancamentoCarteira.referencia == f"pedido:{order.id}"))
    if not owner or not debit or debit.valor_centavos >= 0:
        return jsonify({"erro": "Não há débito de carteira para estornar neste pedido."}), 409
    refund_ref = f"estorno-pedido:{order.id}"
    if not db.session.scalar(select(LancamentoCarteira.id).where(LancamentoCarteira.referencia == refund_ref)):
        applied = lancar_saldo(
            owner.usuario_id,
            -debit.valor_centavos,
            "estorno",
            refund_ref,
            f"Devolução do pedido #{order.id}",
        )
        if applied is None:
            return jsonify({"erro": "Não foi possível devolver o saldo."}), 409
    order.status = "Saldo devolvido"
    db.session.commit()
    return jsonify({"ok": True, "pedido_id": order.id, "valor_centavos": -debit.valor_centavos})


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
    return jsonify({"pedidos": [{
        "id": p.id,
        "tipo": p.tipo_servico or "—",
        "quantidade": p.quantidade,
        "valor_centavos": p.valor_centavos,
        "servico": p.servico_nome,
        "status": p.status,
        "criado": p.data_criacao.isoformat(),
    } for p in pedidos]})


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
            quantidade=payment.quantidade,
            tipo_servico=payment.tipo_servico,
            valor_centavos=payment.valor_centavos,
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
            "QNT": payment.quantidade,
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
    if payment.tipo == "recarga":
        if payment.status != "PAID":
            applied = lancar_saldo(
                payment.usuario_id,
                payment.valor_centavos,
                "recarga",
                f"topup:{payment.merchant_charge_id}",
                "Recarga via InfinitePay",
                received=True,
            )
            if applied is None:
                return False
            payment.status = "PAID"
            db.session.commit()
        return True
    enviar_pedido_apos_pagamento(payment)
    return payment.status == "PAID"


@app.post("/api/carteira/adicionar-saldo")
def criar_recarga_carteira():
    user = require_user()
    if not user:
        return jsonify({"erro": "Entre na sua conta para adicionar saldo."}), 401
    data = request.get_json(silent=True) or {}
    try:
        amount = Decimal(str(data.get("valor", ""))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        amount_cents = int(amount * 100)
        handle = infinitepay_handle()
        infinitepay_headers()
    except (ApiError, InvalidOperation, ValueError):
        return jsonify({"erro": "Informe um valor válido para a recarga."}), 400
    if amount_cents < 500 or amount_cents > 1_000_000:
        return jsonify({"erro": "A recarga deve ser entre R$ 5,00 e R$ 10.000,00."}), 400

    charge_id = str(uuid.uuid4())
    payment = PagamentoPix(
        usuario_id=user.id,
        merchant_charge_id=charge_id,
        status="CREATING",
        tipo="recarga",
        valor_centavos=amount_cents,
        quantidade=1,
        servico_id=0,
        servico_nome="Recarga de saldo",
        tipo_servico="CARTEIRA",
        imei_sn="",
        modelo_aparelho="",
        campos_json="{}",
    )
    db.session.add(payment)
    db.session.commit()
    from datetime import timedelta
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    site_url = os.getenv("PUBLIC_BASE_URL", "https://alntechcell-gsm-site.onrender.com").rstrip("/")
    payload = {
        "handle": handle,
        "items": [{"description": payment.servico_nome, "price": amount_cents, "quantity": 1}],
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


@app.post("/api/pagamentos/pix")
def criar_cobranca_pix():
    return jsonify({"erro": "Adicione saldo na carteira e envie o pedido usando o saldo disponível."}), 410


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
    return jsonify({"status": payment.status, "tipo": payment.tipo, "pedido_id": payment.pedido_id, "valor_centavos": payment.valor_centavos})


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
    user = require_user()
    if not user:
        return jsonify({"erro": "Entre na sua conta para enviar um pedido."}), 401
    data = request.get_json(silent=True) or {}
    service_id = str(data.get("servico_id", "")).strip()
    try:
        quantity = int(data.get("quantidade", 0))
    except (TypeError, ValueError):
        quantity = 0
    if not service_id or quantity < 1 or quantity > 1000:
        return jsonify({"erro": "Escolha um serviço e informe uma quantidade entre 1 e 1.000."}), 400
    try:
        catalog = get_catalog()
        service = next((item for item in catalog["services"] if item["id"] == service_id), None)
        if not service:
            return jsonify({"erro": "Serviço não encontrado. Atualize o catálogo."}), 400
        rate = pix_credit_rate()
        unit_price = Decimal(str(service.get("price", ""))) * rate
        amount_cents = int((unit_price * quantity * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if amount_cents < 1 or amount_cents > 100_000_000:
            return jsonify({"erro": "O valor deste pedido está fora do limite permitido."}), 400
    except (ApiError, InvalidOperation, ValueError) as exc:
        return jsonify({"erro": str(exc)}), 502

    wallet = get_wallet(user.id, lock=True)
    if wallet.saldo_centavos < amount_cents:
        available = wallet.saldo_centavos
        db.session.rollback()
        return jsonify({
            "erro": "Saldo insuficiente. Adicione saldo à sua carteira para concluir o pedido.",
            "saldo_centavos": available,
            "valor_centavos": amount_cents,
        }), 402
    cliente = db.session.scalar(select(Cliente).where(Cliente.whatsapp == user.whatsapp))
    if cliente is None:
        cliente = Cliente(nome=user.nome[:100], whatsapp=user.whatsapp[:30])
        db.session.add(cliente)
        db.session.flush()
    order = Pedido(
        imei_sn="",
        modelo_aparelho="",
        quantidade=quantity,
        tipo_servico=str(service.get("type", ""))[:40],
        valor_centavos=amount_cents,
        servico_id=int(service["id"]),
        servico_nome=service["name"][:180],
        status="Enviando",
        cliente_id=cliente.id,
    )
    db.session.add(order)
    db.session.flush()
    db.session.add(DonoPedido(pedido_id=order.id, usuario_id=user.id))
    wallet.saldo_centavos -= amount_cents
    wallet.data_atualizacao = datetime.now(timezone.utc)
    db.session.add(LancamentoCarteira(
        usuario_id=user.id,
        referencia=f"pedido:{order.id}",
        tipo="pedido",
        valor_centavos=-amount_cents,
        descricao=f"Pedido #{order.id} · {service['name']} · {quantity} un.",
    ))
    db.session.commit()

    parameters = {
        str(order.id): {
            "ID": int(service["id"]),
            "QNT": quantity,
            "CUSTOMFIELD": base64.b64encode(b"{}").decode("ascii"),
        }
    }
    try:
        payload = call_unlockclean("placebulkorder", parameters)
        result = (payload.get("SUCCESS") or {}).get(str(order.id), {})
        if result.get("status") != "success":
            raise ApiError(result.get("message", "O fornecedor não confirmou o processamento."))
    except ApiError as exc:
        order.status = "Em análise"
        order.resposta_api = str(exc)[:1000]
        db.session.commit()
        app.logger.warning("Pedido %s aguarda análise do suporte.", order.id)
        return jsonify({
            "pedido_id": order.id,
            "status": order.status,
            "aviso": "O saldo foi reservado e o pedido está em análise. Consulte o painel ou fale com o suporte.",
            "carteira": wallet_summary(user),
        }), 202
    order.referencia_api = str(result.get("referenceid", "")) or None
    order.status = "Pendente"
    order.resposta_api = json.dumps(result, ensure_ascii=False)
    db.session.commit()
    return jsonify({
        "pedido_id": order.id,
        "status": order.status,
        "valor_centavos": amount_cents,
        "carteira": wallet_summary(user),
    }), 201


@app.post("/api/pedidos/consultar")
def consultar_pedidos():
    user = require_user()
    if not user:
        return jsonify({"mensagem": "Entre na sua conta para consultar pedidos."}), 401
    pedidos = db.session.scalars(
        select(Pedido).join(DonoPedido, DonoPedido.pedido_id == Pedido.id)
        .where(DonoPedido.usuario_id == user.id).order_by(Pedido.data_criacao.desc()).limit(250)
    ).all()
    return jsonify({"pedidos": [{
        "id": p.id, "tipo": p.tipo_servico or "—", "quantidade": p.quantidade,
        "valor_centavos": p.valor_centavos, "servico": p.servico_nome,
        "status": p.status, "criado": p.data_criacao.isoformat(),
    } for p in pedidos]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
