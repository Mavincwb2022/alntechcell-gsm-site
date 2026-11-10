# ALNTECH CELL — integração UnlockCleaning

Aplicação Flask com catálogo dinâmico, envio de pedidos e consulta de status pela API pública UnlockCleaning.

## Configuração

O usuário da API está configurado em `app.py`. A chave é lida de `UNLOCKCLEAN_API_KEY` e deve ser adicionada como segredo no Render; ela não faz parte do código nem do Blueprint. Se necessário, `UNLOCKCLEAN_USERNAME` pode substituir o e-mail configurado. O servidor precisa permitir conexões HTTPS de saída.

Variáveis opcionais:

- `UNLOCKCLEAN_PUBLIC_URL`: padrão `https://unlockingclean.com/public`
- `DATABASE_URL`: padrão SQLite local; use a URL PostgreSQL do provedor de hospedagem em produção.
- `PORT`: padrão `5000`.

Instale dependências com `pip install -r requirements.txt` e inicie com `python app.py`. Para produção, o `Procfile` usa Gunicorn.

O site carrega os serviços diretamente da API e guarda pedidos e referências da API no banco local. A consulta de pedido exige IMEI/serial e o mesmo WhatsApp informado no cadastro.

## Publicação temporária no Render

O `render.yaml` cria o site `alntechcell-gsm-site` e um PostgreSQL gratuito temporário, ambos nos planos gratuitos. O banco gratuito expira após 30 dias; o serviço web gratuito também pode suspender quando fica ocioso. Use apenas para o teste temporário solicitado.

Logo e banner da marca ficam em `static/logo.png` e `static/hero-banner.png`.
