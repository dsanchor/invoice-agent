# Invoice Agent

Servicio Python que publica un agente de facturas de Microsoft Agent Framework mediante A2A JSON-RPC. Usa un proveedor compatible con la API de OpenAI y function tools locales sobre datos mock.

## Configuracion

1. Copia `.env.example` a `.env`.
2. Configura `OPENAI_MODEL` y la URL compatible con OpenAI publicada por APIM en `OPENAI_BASE_URL`.
3. Configura en `OPENAI_TOKEN_SCOPE` el scope de la API protegida por APIM, por ejemplo `api://<application-client-id>/.default`.
4. Inicia sesion con `az login` para desarrollo local.

El agente obtiene y renueva el JWT mediante `DefaultAzureCredential`. En Azure Container Apps, habilita una identidad administrada y asignale el rol o permiso de aplicacion requerido por la API de APIM. No se necesita `OPENAI_API_KEY`.

`AGENT_PUBLIC_URL` debe ser la URL publica anunciada a los clientes. Cuando APIM este delante del agente, usa por ejemplo `https://<apim>.azure-api.net/a2a/invoice/`.

## Ejecucion local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Endpoints:

- `GET /health`
- `GET /.well-known/agent-card.json`
- `POST /` para A2A JSON-RPC

Test:

```bash
curl -X POST http://localhost:8081/   -H 'Content-Type: application/json'   -H 'A2A-Version: 1.0'   --data-binary '{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":{"role":"ROLE_USER","messageId":"msg-1","parts":[{"text":"Show me all invoices for Contoso"}]}}}'
```

## Docker

```bash
docker build -t invoice-agent:local .
docker run --rm -p 8080:8080 \
  --env-file .env \
  invoice-agent:local
```

La autenticacion interactiva local no se transmite automaticamente al contenedor. Para pruebas en Docker proporciona una credencial admitida por `DefaultAzureCredential`; en Container Apps utiliza identidad administrada.

## Despliegue manual en Azure Container Apps

Estos pasos crean la aplicacion en un Container Apps Environment existente usando la imagen publicada en GHCR. Define primero los valores del entorno:

```bash
az login
az extension add --name containerapp --upgrade

SUBSCRIPTION_ID="<subscription-id>"
RESOURCE_GROUP="<resource-group>"
CONTAINERAPPS_ENVIRONMENT="<container-apps-environment>"
CONTAINER_APP_NAME="invoice-agent"
IMAGE="ghcr.io/dsanchor/invoice-agent:latest"
OPENAI_MODEL="<model-name>"
OPENAI_BASE_URL="https://<apim-name>.azure-api.net/<openai-path>/v1"
OPENAI_TOKEN_SCOPE="api://<application-client-id>/.default"

az account set --subscription "$SUBSCRIPTION_ID"
ENVIRONMENT_ID=$(az containerapp env show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINERAPPS_ENVIRONMENT" \
  --query id \
  --output tsv)
```

Crea la Container App con ingress externo, identidad administrada y el puerto del contenedor:

```bash
az containerapp create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --environment "$ENVIRONMENT_ID" \
  --image "$IMAGE" \
  --ingress external \
  --target-port 8080 \
  --system-assigned \
  --env-vars \
    PORT=8080 \
    LOG_LEVEL=INFO \
    OPENAI_MODEL="$OPENAI_MODEL" \
    OPENAI_BASE_URL="$OPENAI_BASE_URL" \
    OPENAI_TOKEN_SCOPE="$OPENAI_TOKEN_SCOPE"
```

Obtiene la URL asignada y actualiza la URL publica anunciada en el Agent Card:

```bash
FQDN=$(az containerapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --query properties.configuration.ingress.fqdn \
  --output tsv)

az containerapp update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --set-env-vars AGENT_PUBLIC_URL="https://$FQDN/"

curl --fail "https://$FQDN/health"
curl --fail "https://$FQDN/.well-known/agent-card.json"
```

La identidad administrada debe tener el permiso de aplicacion que APIM valida para `OPENAI_TOKEN_SCOPE`. Puedes obtener su principal ID con:

```bash
az containerapp identity show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --query principalId \
  --output tsv
```

## APIM y produccion

Este servicio no autentica peticiones A2A. Configura APIM para validar JWT de Entra ID, aplicar autorizacion, limites de consumo y correlacion antes de reenviar al Container App. Restringe el ingress para que el backend no pueda eludir APIM.

El almacenamiento de tareas y sesiones es en memoria, adecuado solo para la demo con una replica. Antes de escalar a varias replicas se debe incorporar almacenamiento persistente y vincular la propiedad de tareas y sesiones a una identidad autenticada, no a los IDs recibidos en el protocolo A2A.