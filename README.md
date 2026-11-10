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

## Contas e pagamentos

- Clientes criam conta com nome, e-mail, WhatsApp e senha (armazenada como hash). Os pedidos ficam vinculados à conta; só ela e a conta administradora podem ver os dados correspondentes.
- Para criar a conta administradora uma única vez, defina `ADMIN_SETUP_TOKEN` como secret no Render, abra `/admin/setup`, digite o token e escolha uma senha de pelo menos 12 caracteres. O e-mail administrativo configurado é `alntechunlocker@gmail.com`. O administrador não deve compartilhar a senha com o suporte.
- O checkout Pix usa a API oficial do PicPay no backend. Configure no Render `PICPAY_CLIENT_ID`, `PICPAY_CLIENT_SECRET` e `PICPAY_WEBHOOK_TOKEN`; não coloque esses valores no GitHub nem no chat. Configure no Painel Lojista do PicPay o webhook `https://alntechcell-gsm-site.onrender.com/api/picpay/webhook` e use o token exibido pelo PicPay.
- O preço enviado ao PicPay é calculado com a regra `PIX_CREDIT_RATE_BRL=1`: um crédito do catálogo equivale a R$ 1,00. O pedido ao fornecedor só é enviado após o PicPay confirmar o pagamento.
- `FLASK_SECRET_KEY` deve ser gerada e mantida no Render para preservar sessões entre reinicializações. O Blueprint define geração automática para essa chave quando sincronizado.
- O PicPay exige dados do pagador, incluindo CPF para esta integração. O CPF é enviado à API para criar a cobrança e não é armazenado na tabela de contas ou pedidos.

Logo e banner da marca ficam em `static/logo.png` e `static/hero-banner.png`.
