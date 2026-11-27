# Cuaderno conectado con LoTW, eQSL y QRZ

Radio Guilda puede **subir** los contactos del cuaderno a LoTW (ARRL), eQSL y QRZ Logbook e **importar sus confirmaciones** (QSL recibidas).
Está **desactivado mientras no haya credenciales**: sin ellas no se llama a ningún servicio. Código: `backend/meteoguilda/qsl_servicios.py`;
pruebas contra servidores simulados: `backend/tests/test_qsl_servicios.py`.

## Cómo activarlo (lo hace cada persona, en la web)
Requisitos del servidor: `MG_SECRET_KEY` en el `.env` (la genera `desplegar_produccion.sh`; es la misma que cifra las claves de IA). Nada más.

1. Entra en https://radio.guildawork.com → **Radio → Cuaderno QSL** y activa la sincronización (icono de llave) si aún no lo está:
   subir y confirmar trabajan sobre el cuaderno sincronizado.
2. En **Servicios**, pulsa **Conectar** en el que quieras y escribe:
   - **QRZ Logbook**: la *API key* del logbook (QRZ → Logbook → Settings; tiene la forma `XXXX-XXXX-XXXX-XXXX` y requiere una suscripción de QRZ que la incluya).
   - **eQSL**: usuario y contraseña de eQSL.cc y, si tienes varias ubicaciones, el *QTH Nickname*.
   - **LoTW**: usuario y contraseña **de la web** de LoTW (para bajar las confirmaciones). Para **subir** hace falta además el certificado de la ARRL
     exportado desde TQSL como fichero `.p12` (TQSL → Callsign Certificates → Save Callsign Certificate File), su contraseña y los datos de la estación
     (indicativo, entidad DXCC, locator, zona CQ y zona ITU; España peninsular y Baleares: 281, 14 y 37).
3. **Guardar cifrado**. Desde ese momento aparecen los botones **Subir pendientes** y **Bajar confirmaciones**.

Las mismas acciones (no las credenciales) están en la API y el MCP:
| Acción | API | MCP |
|---|---|---|
| Estado de los servicios | `GET /api/qsl/servicios` | `qsl_services_status` |
| Subir los contactos pendientes (hasta 300 por llamada) | `POST /api/qsl/{lotw\|eqsl\|qrz}/subir` (con `Authorization: Bearer <clave del cuaderno>`) | `qsl_upload` |
| Bajar confirmaciones (desde la última vez; `?desde_cero=1` todas) | `POST /api/qsl/{servicio}/confirmaciones` | `qsl_import_confirmations` |
| Guardar / quitar credenciales | `PUT\|DELETE /api/qsl/servicios/{servicio}` **solo con la sesión de la web** | — (a propósito) |

## Seguridad y privacidad
- Las credenciales se cifran con Fernet (`MG_SECRET_KEY`) en la tabla `qsl_credenciales` y **no se devuelven nunca**: la API solo dice si están, el
  usuario y los 4 últimos caracteres de la clave de QRZ. Si se pierde `MG_SECRET_KEY`, hay que volver a escribirlas.
- **No pasan por el MCP ni por la identidad delegada** (`X-Act-As`): `PUT/DELETE /api/qsl/servicios/*` exigen la cookie de la web y responden 403 al MCP.
- Se borran con «Quitar» y al borrar la cuenta (`DELETE /api/me`); la descarga de datos (`/api/me/datos`) dice qué servicios hay, sin las credenciales.
- Qué se ha subido o confirmado queda en cada contacto (`qsos.servicios`) y una confirmación marca la QSL como recibida.

## Cómo funciona cada servicio
| Servicio | Subir | Confirmaciones | Errores habituales |
|---|---|---|---|
| QRZ | `POST https://logbook.qrz.com/api` `ACTION=INSERT` (un contacto por petición; «duplicate» cuenta como ya subido) | `ACTION=FETCH`, `OPTION=TYPE:ADIF,STATUS:CONFIRMED,MODSINCE:…` | `RESULT=AUTH`: clave mala o sin suscripción |
| eQSL | `POST https://www.eqsl.cc/qslcard/ImportADIF.cfm` (`ADIFData` con `EQSL_USER`/`EQSL_PSWD` en la cabecera) | `DownloadInBox.cfm?RcvdSince=…` y el `.adi` que enlaza | «No match on eQSL_User/eQSL_Pswd» |
| LoTW | Fichero `.tq8` firmado (formato GABBI de TQSL: certificado, estación y cada contacto con `SIGN_LOTW_V2.0` RSA-SHA1 sobre `SIGNDATA`) a `https://lotw.arrl.org/lotw/upload` | `lotwreport.adi?qso_query=1&qso_qsl=yes&qso_qslsince=…` | «.UPL. rejected»; respuesta sin `<eoh>` = usuario o contraseña mal |

Una confirmación casa con un contacto si coinciden el indicativo y la banda y la hora difiere como mucho 30 minutos.

**LoTW, firma experimental.** La firma reproduce el formato de TQSL (campos de estación por orden alfabético — CQZ, GRIDSQUARE, ITUZ — seguidos de
BAND, CALL, FREQ, MODE, QSO_DATE y QSO_TIME, en mayúsculas) y se ha comprobado solo contra un servidor simulado que verifica la firma con el certificado.
Con la primera subida real, mira el resultado: si LoTW la rechaza, el contacto queda como pendiente; exporta el ADIF (botón ADIF) y fírmalo con TQSL,
y avisa para corregir el formato. La bajada de confirmaciones no depende de la firma.

## Límites
- Hasta 300 contactos por subida; se vuelve a pulsar para el resto (los ya subidos no se repiten).
- No hay sincronización automática en segundo plano: se sube y se baja cuando la persona lo pide (web, API o MCP).
