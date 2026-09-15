# Invoice Agent

Servicio Python que publica un agente de facturas de Microsoft Agent Framework mediante A2A JSON-RPC. Usa Microsoft Foundry como proveedor del modelo y function tools locales sobre datos mock.

## Configuracion

1. Copia `.env.example` a `.env`.
2. Configura `FOUNDRY_PROJECT_ENDPOINT` y `FOUNDRY_MODEL`.
3. Inicia sesion con `az login` para desarrollo local.

En Azure Container Apps, habilita una identidad administrada y concedele acceso al proyecto de Foundry. `DefaultAzureCredential` la utilizara automaticamente; no incluyas credenciales en variables ni en la imagen.

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

Hay peticiones de ejemplo en `requests.http`.

## Docker

```bash
docker build -t invoice-agent:local .
docker run --rm -p 8080:8080 \
  --env-file .env \
  invoice-agent:local
```

La autenticacion interactiva local no se transmite automaticamente al contenedor. Para una prueba local en Docker configura un mecanismo de credenciales admitido por `DefaultAzureCredential`; en Container Apps utiliza identidad administrada.

## APIM y produccion

Este servicio no autentica peticiones A2A. Configura APIM para validar JWT de Entra ID, aplicar autorizacion, limites de consumo y correlacion antes de reenviar al Container App. Restringe el ingress para que el backend no pueda eludir APIM.

El almacenamiento de tareas y sesiones es en memoria, adecuado solo para la demo con una replica. Antes de escalar a varias replicas se debe incorporar almacenamiento persistente y vincular la propiedad de tareas y sesiones a una identidad autenticada, no a los IDs recibidos en el protocolo A2A.