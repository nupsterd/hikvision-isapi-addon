ARG BUILD_FROM
FROM $BUILD_FROM

# Metadatos
LABEL maintainer="nupsterd"
LABEL description="Hikvision ISAPI Event Stream Listener for Home Assistant"

# Instalar Python 3 y dependencias del sistema
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-requests

# Setup del directorio de trabajo
WORKDIR /app

# Copiar el código del listener
COPY hikvision_isapi/ /app/hikvision_isapi/

# Buena práctica: ejecutar como módulo, no como script
CMD ["python3", "-m", "hikvision_isapi.listener"]