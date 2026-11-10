import base64
import json
import os
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import select

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///local.db")
if app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgres://"):
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config["SQLALCHEMY_DATABASE_URI"].replace(
        "postgres://", "postgresql://", 1
    )
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Keep the API access key in the hosting provider's secret environment settings.
UNLOCKCLEAN_API_KEY = os.getenv("UNLOCKCLEAN_API_KEY", "")
UNLOCKCLEAN_USERNAME = os.getenv("UNLOCKCLEAN_USERNAME", "alntechunlock@gmail.com")
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


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/servicos")
def api_servicos():
    try:
        return jsonify(get_catalog())
    except ApiError as exc:
        return jsonify({"erro": str(exc)}), 502


@app.post("/api/pedidos")
def criar_pedido():
    data = request.get_json(silent=True) or {}
    required = ("nome", "whatsapp", "modelo", "imei_sn", "servico_id")
    if any(not str(data.get(key, "")).strip() for key in required):
        return jsonify({"erro": "Preencha todos os campos obrigatórios."}), 400

    try:
        catalog = get_catalog()
        service = next((s for s in catalog["services"] if s["id"] == str(data["servico_id"])), None)
        if service is None:
            return jsonify({"erro": "Esse serviço não está mais disponível. Atualize a página."}), 400

        whatsapp = str(data["whatsapp"]).strip()
        cliente = db.session.scalar(select(Cliente).where(Cliente.whatsapp == whatsapp))
        if cliente is None:
            cliente = Cliente(nome=str(data["nome"]).strip()[:100], whatsapp=whatsapp[:30])
            db.session.add(cliente)
            db.session.flush()

        pedido = Pedido(
            imei_sn=str(data["imei_sn"]).strip()[:80],
            modelo_aparelho=str(data["modelo"]).strip()[:100],
            servico_id=int(service["id"]),
            servico_nome=service["name"][:180],
            cliente_id=cliente.id,
        )
        db.session.add(pedido)
        db.session.flush()

        fields = data.get("campos") if isinstance(data.get("campos"), dict) else {}
        params = {
            str(pedido.id): {
                "ID": int(service["id"]),
                "QNT": 1,
                "CUSTOMFIELD": base64.b64encode(
                    json.dumps(fields, ensure_ascii=False).encode("utf-8")
                ).decode("ascii"),
            }
        }
        payload = call_unlockclean("placebulkorder", params)
        result = (payload.get("SUCCESS") or {}).get(str(pedido.id), {})
        if result.get("status") != "success":
            raise ApiError(result.get("message", "A API não confirmou o pedido."))

        pedido.referencia_api = str(result.get("referenceid", "")) or None
        pedido.status = "Pendente"
        pedido.resposta_api = json.dumps(result, ensure_ascii=False)
        db.session.commit()
        return jsonify({"mensagem": f"Pedido #{pedido.id} enviado com sucesso!", "pedido_id": pedido.id}), 201
    except (ApiError, ValueError) as exc:
        db.session.rollback()
        return jsonify({"erro": str(exc)}), 502
    except Exception:
        db.session.rollback()
        app.logger.exception("Falha ao criar pedido")
        return jsonify({"erro": "Não foi possível registrar o pedido."}), 500


@app.post("/api/pedidos/consultar")
def consultar_pedidos():
    data = request.get_json(silent=True) or {}
    imei_sn = str(data.get("imei_sn", "")).strip()
    whatsapp = str(data.get("whatsapp", "")).strip()
    if not imei_sn or not whatsapp:
        return jsonify({"mensagem": "Informe o IMEI/serial e o WhatsApp usado no pedido."}), 400

    pedidos = db.session.scalars(
        select(Pedido).join(Cliente).where(
            Pedido.imei_sn == imei_sn, Cliente.whatsapp == whatsapp
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
