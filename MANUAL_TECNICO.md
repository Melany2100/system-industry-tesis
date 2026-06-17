# Manual tecnico del Sistema de Monitoreo de Riesgos Industriales (SMRI)

## 1. Proposito y alcance

El SMRI es una aplicacion web para monitorear camaras, reconocer personas autorizadas,
detectar riesgos y uso de equipos de proteccion personal (EPP), registrar evidencias,
notificar incidentes por correo y generar informes en PDF o Excel.

Los modulos principales son:

- autenticacion, usuarios y roles de administrador u operador;
- administracion y transmision de camaras locales o de red;
- reconocimiento facial de personas autorizadas;
- deteccion de objetos peligrosos y EPP mediante modelos YOLO;
- registro, revision y resolucion de eventos de seguridad;
- almacenamiento de evidencias fotograficas;
- notificacion de incidentes por correo electronico;
- consulta y exportacion de informes.

## 2. Arquitectura general

La solucion utiliza una arquitectura web monolitica basada en Django:

1. El navegador consume vistas HTML, archivos CSS/JavaScript y endpoints JSON.
2. Django gestiona autenticacion, reglas de negocio, eventos e informes.
3. OpenCV obtiene y procesa los cuadros de las camaras.
4. `face_recognition`, YOLOv3-tiny y Ultralytics procesan reconocimiento facial,
   objetos de riesgo y EPP.
5. PostgreSQL conserva usuarios, personas, camaras, eventos e informes.
6. Django Storage guarda fotografias de rostros y evidencias en el sistema de archivos.
7. El servidor SMTP configurado envia las alertas de incidentes.

## 3. Requerimientos funcionales del sistema

- RF-01: permitir el inicio y cierre de sesion de usuarios registrados.
- RF-02: diferenciar permisos de administradores y operadores.
- RF-03: registrar personas autorizadas con nombres, correo, cargo y fotografia facial.
- RF-04: configurar camaras locales o fuentes de video compatibles con OpenCV/FFmpeg.
- RF-05: visualizar el flujo de las camaras activas.
- RF-06: reconocer personas autorizadas y detectar rostros desconocidos.
- RF-07: detectar objetos peligrosos y condiciones relacionadas con EPP.
- RF-08: crear eventos con tipo, severidad, fecha, camara, persona y evidencia.
- RF-09: consultar, revisar, resolver y filtrar eventos de seguridad.
- RF-10: enviar una alerta de correo al trabajador identificado y una copia a los
  administradores registrados.
- RF-11: registrar el resultado del correo como pendiente, enviado, fallido u omitido.
- RF-12: permitir que un administrador reintente un correo fallido.
- RF-13: generar y exportar informes en PDF y Excel.

## 4. Requerimientos no funcionales

- Seguridad: las vistas privadas requieren autenticacion y las funciones sensibles
  validan el rol de administrador.
- Trazabilidad: cada incidente conserva estado, destinatario, fecha de envio y error.
- Rendimiento: el procesamiento de video debe limitar los FPS de acuerdo con el equipo.
- Disponibilidad: PostgreSQL, el almacenamiento, las camaras y SMTP deben estar
  accesibles para que todas las funciones operen.
- Privacidad: las imagenes faciales y evidencias deben tener acceso restringido,
  respaldo y una politica de retencion.
- Compatibilidad: el navegador debe soportar HTML5, JavaScript y streaming MJPEG.
- Zona horaria e idioma: `America/Guayaquil` y espanol de Ecuador (`es-ec`).

## 5. Requerimientos de hardware

### Ambiente minimo de desarrollo o demostracion

- procesador de 4 nucleos de 64 bits;
- 8 GB de memoria RAM;
- 10 GB libres, adicionales al crecimiento de evidencias;
- una camara USB o una fuente IP/RTSP compatible;
- conexion de red para camaras IP y correo SMTP.

### Ambiente recomendado de operacion

- procesador de 6 a 8 nucleos;
- 16 GB de RAM o mas;
- almacenamiento SSD de 50 GB o mas, con monitoreo y respaldo;
- red cableada estable para camaras;
- GPU NVIDIA compatible con PyTorch/CUDA, opcional pero recomendable cuando se
  procesan varias camaras o se requiere mayor cantidad de FPS.

La capacidad final depende del numero de camaras, resolucion, FPS, modelos activos y
tiempo de conservacion de evidencias. Deben realizarse pruebas de carga con las camaras
reales antes de fijar el dimensionamiento productivo.

## 6. Requerimientos de software

- sistema operativo de 64 bits: Windows 10/11 para el entorno actual, o Linux para
  un servidor de produccion;
- Python 3.10 o una version compatible con Django 4.2 y las librerias de vision;
- PostgreSQL 13 o superior recomendado;
- `pip` y un entorno virtual de Python;
- controladores de la camara y soporte de OpenCV/FFmpeg;
- compilador C/C++ y CMake cuando `dlib` o `face_recognition` deban compilarse;
- navegador actualizado: Chrome, Edge o Firefox;
- servidor SMTP con TLS o SSL y credenciales de aplicacion;
- Node.js y npm solamente si se recompilan los recursos del tema frontend.

## 7. Frameworks y librerias principales

| Componente | Version del proyecto | Funcion |
| --- | ---: | --- |
| Django | 4.2.30 | Framework web, ORM, autenticacion y plantillas |
| Django Channels | 4.3.2 | Infraestructura de canales; actualmente usa una capa en memoria |
| PostgreSQL / psycopg2 | 2.9.12 | Base de datos relacional y controlador Python |
| OpenCV | 4.13.0.92 | Captura, procesamiento y codificacion de video |
| NumPy | 2.2.6 | Operaciones numericas sobre imagenes |
| face_recognition / dlib | 1.3.0 / 20.0.1 | Deteccion y codificacion facial |
| Ultralytics | 8.4.60 | Ejecucion del modelo YOLO para EPP |
| PyTorch / torchvision | 2.12.0 / 0.27.0 | Motor de inferencia de modelos de vision |
| Pillow | 12.2.0 | Validacion y normalizacion de fotografias |
| ReportLab | 4.5.1 | Exportacion de informes PDF |
| openpyxl | 3.1.5 | Exportacion de informes Excel |
| python-dotenv | 1.2.2 | Lectura de configuracion desde `.env` |
| Bootstrap | 5.0.2 | Interfaz web adaptable |
| Chartist | 0.11.4 | Graficos del panel |

El repositorio tambien incluye los modelos `camera/ppe.pt` y YOLOv3-tiny
(`.weights`, `.cfg` y `coco.names`). Estos archivos son necesarios para las detecciones
implementadas por el modulo de camara.

## 8. Entornos de despliegue

### Desarrollo local actual

- Windows y script `start_smri.bat`;
- servidor integrado `python manage.py runserver 0.0.0.0:8000`;
- PostgreSQL local en el puerto 5432;
- evidencias bajo `media/`;
- inicio automatico de camaras a 8 FPS;
- `DEBUG=True` y capa de Channels en memoria.

Este entorno es adecuado para desarrollo y demostracion, no para exposicion publica.

### Produccion recomendada

- servidor Linux de 64 bits;
- aplicacion Django ejecutada por Gunicorn para WSGI o Daphne/Uvicorn para ASGI;
- Nginx como proxy inverso, terminacion HTTPS y servicio de archivos estaticos/media;
- PostgreSQL en un servicio protegido y con copias de seguridad;
- Redis como capa de canales si se habilitan procesos o conexiones en tiempo real;
- almacenamiento persistente para evidencias, local protegido o compatible con S3;
- servicio SMTP transaccional o corporativo;
- servicio del sistema o contenedores para reinicio automatico y registros;
- firewall, monitoreo de CPU/RAM/disco y rotacion de logs.

Antes de produccion se debe configurar `DEBUG=False`, `ALLOWED_HOSTS`, una
`SECRET_KEY` externa, credenciales de base de datos externas, HTTPS, cookies seguras,
proteccion CSRF y una politica de copias y retencion de evidencias.

## 9. Instalacion y puesta en marcha

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Se debe crear la base PostgreSQL y ajustar la configuracion antes de ejecutar las
migraciones. En Windows tambien se puede usar `start_smri.bat`, que aplica migraciones,
habilita el inicio de camaras y levanta el servidor en el puerto 8000.

## 10. Configuracion del correo

El proyecto no implementa una API publica o un proveedor de correo propio. Implementa
un servicio interno de Django que se conecta a un servidor SMTP mediante estas variables:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=cuenta.smtp@example.com
EMAIL_HOST_PASSWORD=clave_o_token_de_aplicacion
DEFAULT_FROM_EMAIL=Sistema SMRI <cuenta.smtp@example.com>
EMAIL_TIMEOUT=10
```

No se deben almacenar claves reales en Git. Para Gmail se utiliza una contrasena de
aplicacion; en produccion es preferible un servicio SMTP corporativo o transaccional.

### Destinatarios actuales

El servicio puede enviar correos a diferentes usuarios porque cada evento obtiene el
destinatario desde `AuthorizedPerson.correo`. Su comportamiento es:

1. Destinatario principal (`To`): correo de la persona autorizada asociada al evento.
2. Copia (`CC`): usuarios con `is_superuser`, `is_staff` o pertenecientes a grupos de
   administracion, siempre que tengan correo.
3. Contenido: persona, incidente, detalle, severidad, camara, fecha y evidencia adjunta.
4. Disparo: se ejecuta automaticamente despues de confirmar la creacion del evento.
5. Reintento: un administrador puede usar
   `POST /camera/security-events/<id>/retry-email/`.

### Alcance y limitaciones

- Si dos incidentes corresponden a dos personas con correos distintos, cada persona
  recibe su propio mensaje.
- Un incidente solo admite un destinatario principal, mas las copias administrativas.
- No existe un endpoint para indicar libremente una lista de destinatarios, asunto y
  contenido; por seguridad, el servidor determina esos datos.
- Si la persona no tiene correo, el envio queda como `SKIPPED`.
- Si el backend esta en modo consola o SMTP falla, queda como `FAILED` y puede reintentarse.
- La preferencia de usuario `email_alerts` se guarda, pero actualmente no interviene en
  la decision de envio: todo evento creado intenta notificar a la persona asociada.
- El envio se realiza dentro del proceso web. Para mayor volumen o tolerancia a fallos
  se recomienda una cola de tareas como Celery con Redis o RabbitMQ.

## 11. Verificacion tecnica

```powershell
python manage.py check
python manage.py test core_apps.camera
```

Tambien deben verificarse manualmente la conexion de cada camara, la entrega SMTP, el
acceso restringido a evidencias y la exportacion de informes PDF/Excel.

## 12. Observaciones de seguridad pendientes

- mover `SECRET_KEY` y las credenciales PostgreSQL de `config/settings.py` al entorno;
- definir `ALLOWED_HOSTS` y desactivar `DEBUG` en produccion;
- rotar cualquier credencial que haya sido compartida o versionada anteriormente;
- separar configuraciones de desarrollo y produccion;
- proteger y respaldar la carpeta de evidencias;
- revisar si `email_alerts` debe controlar los envios o eliminarse para evitar ambiguedad.
