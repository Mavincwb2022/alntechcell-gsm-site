# ALNTECH CELL — integração UnlockCleaning

Aplicação Flask com catálogo dinâmico, envio de pedidos e consulta de status pela API pública UnlockCleaning.

## Configuração

O usuário da API está configurado em `app.py`. A chave é lida de `UNLOCKCLEAN_API_KEY` e deve ser adicionada como segredo no Render; ela não faz parte do código nem do Blueprint. Se necessário, `UNLOCKCLEAN_USERNAME` pode substituir o e-mail configurado. O servidor precisa permitir conexões HTTPS de saída.

Variáveis opcionais:

- `UNLOCKCLEAN_PUBLIC_URL`: padrão `https://unlockingclean.com/public`
- `DATABASE_URL`: padrão SQLite local; use a URL PostgreSQL do provedor de hospedagem em produção.
- `PORT`: padrão `5000`.

Instale dependências com `pip install -r requirements.txt` e inicie com `python app.py`. Para produção, o `Procfile` usa Gunicorn.

O site carrega os serviços diretamente da API, mantém uma carteira interna por conta e guarda pedidos, quantidades e referências da API no banco. Para enviar um pedido o cliente entra na conta, escolhe o serviço e informa a quantidade; o IMEI, modelo e telefone não são solicitados no formulário do pedido. O histórico fica em `/minha-conta/pedidos`.

## Publicação temporária no Render

O `render.yaml` cria o site `alntechcell-gsm-site` e um PostgreSQL gratuito temporário, ambos nos planos gratuitos. O banco gratuito expira após 30 dias; o serviço web gratuito também pode suspender quando fica ocioso. Use apenas para o teste temporário solicitado.

## Contas e pagamentos

- Clientes criam conta com nome, e-mail, WhatsApp e senha (armazenada como hash). Os pedidos ficam vinculados à conta; só ela e a conta administradora podem ver os dados correspondentes.
- Para criar a conta administradora uma única vez, defina `ADMIN_SETUP_TOKEN` como secret no Render, abra `/admin/setup`, digite o token e escolha uma senha de pelo menos 12 caracteres. O e-mail administrativo configurado é `alntechunlocker@gmail.com`. O administrador não deve compartilhar a senha com o suporte.
- A carteira interna aceita recarga pela API oficial da InfinitePay. Configure `INFINITEPAY_API_KEY` como secret e `INFINITEPAY_HANDLE` com sua InfiniteTag sem `$`. As credenciais nunca devem ir ao GitHub, HTML/JavaScript público ou chat. O saldo só é creditado depois da confirmação de pagamento via `/payment_check`.
- Pedidos pagos pela carteira debitam o preço unitário vezes a quantidade e são encaminhados à API UnlockCleaning. A solicitação é enviada sem IMEI, modelo ou campos extras; serviços que dependam desses dados podem ficar em análise. O painel administrativo pode devolver o saldo de pedidos em análise ou recusados depois da conferência do suporte.
- Cada link de recarga usa pagamento único e expira em uma hora. O webhook da integração é `https://alntechcell-gsm-site.onrender.com/api/infinitepay/webhook` e o retorno é `/checkout/retorno`.
- O preço exibido e cobrado usa `PIX_CREDIT_RATE_BRL=1.70`: cada crédito do catálogo custa R$ 1,70. Como a referência anterior era R$ 1,00 por crédito, isso aplica um acréscimo de 70%. O cálculo é arredondado para centavos e usa o mesmo valor no catálogo e na cobrança.
- `FLASK_SECRET_KEY` deve ser gerada e mantida no Render para preservar sessões entre reinicializações. O Blueprint define geração automática para essa chave quando sincronizado.

Logo e banner da marca ficam em `static/logo.png` e `static/hero-banner.png`.
