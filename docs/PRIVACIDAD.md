# Privacidad y RGPD · Meteo Guilda / Radio Guilda

Resumen de qué datos personales trata la aplicación, para qué, durante cuánto tiempo y cómo se ejercen los derechos. El responsable es **Guilda Work**.
Todo lo que sigue es **opcional y desactivado por defecto**, salvo la sesión (necesaria para usar la web).

| Dato | Para qué | Base legal | Dónde | Conservación | Cómo se borra |
|---|---|---|---|---|---|
| Sesión (identificador de cuenta y correo de Guilda Work) | Acceder y separar los ajustes de cada persona | Ejecución del servicio | `web_sessions`, `user_settings` | Mientras dure la sesión / la cuenta | Cerrar sesión; «Borrar mis ajustes y claves» |
| Ajustes: IA, modelo, «Mi ubicación» (redondeada a ~100 m) | Respuestas sobre «aquí» y elección de proveedor | Ejecución del servicio | `user_settings` | Hasta que la persona los borre | Ajustes → Cuenta |
| Claves de API propias (cifradas con Fernet) | Usar el proveedor de IA elegido | Consentimiento (las introduce ella) | `user_settings.keys` | Hasta que las quite | «Quitar clave»; «Borrar mis ajustes y claves» |
| **Memoria del asistente** (notas de preferencias) | Adaptar las respuestas | **Consentimiento** (opt-in) | `user_settings.settings.memoria` | **12 meses** por nota | Borrar nota / todo; desactivar la memoria; borrar la cuenta |
| **Notificaciones push** (endpoint del navegador, claves de cifrado, ubicación a ~1 km, preferencias) | Avisos oficiales y recordatorios de pasos | **Consentimiento** (permiso del navegador + activarlo en Ajustes) | `push_subs`, `push_cola` | Hasta desactivarlas; **90 días** sin entregas correctas | Desactivar en Ajustes → Avisos; borrar la cuenta |
| **Alertas de radio y salidas** (tipo, parámetros con el lugar a ~1 km, tope diario, silencio y zona horaria, títulos de lo enviado) | Avisar de lo que la persona pide | **Consentimiento** (las crea ella; llegan por las notificaciones) | `alertas`, `alertas_prefs`, `alertas_envios` | Hasta que las borre; lo enviado, **30 días** | Borrar en Ajustes → Avisos; borrar la cuenta |
| Rutas guardadas (nombre y puntos de paso) | Alertas en ruta y planificador de salidas | Ejecución del servicio | `rutas_guardadas` | Hasta que la persona las borre (máx. 20) | Rutas → «Mis rutas guardadas»; borrar la cuenta |
| Cuaderno QSL y favoritos | Funciones de radio y lugares | Ejecución del servicio | Perfil de sincronización (`profiles`, `qsos`) | Hasta que la persona los borre | Ajustes → Sincronización y API |

## Memoria del asistente (2.5)
- **Opt-in**: viene apagada. Al activarla se guarda la fecha del consentimiento; retirarlo (apagarla) **borra todo lo recordado**.
- **El asistente nunca guarda nada solo**: propone una nota con un botón «Recordar» y solo se guarda cuando la persona lo pulsa (o la escribe ella en Ajustes → Memoria).
- **Minimización**: máximo 20 notas de 140 caracteres, por categorías (actividades, lugares, radio, unidades, estilo, otras).
- **Datos que se rechazan** (el servidor lo comprueba, no solo el modelo): categorías especiales del art. 9 (salud, creencias, ideología, origen, orientación sexual…) e identificadores (correos, teléfonos, enlaces, DNI, IBAN, claves). Es un filtro de mejor esfuerzo.
- **Destinatarios**: las notas activas se incluyen en el contexto de cada consulta al proveedor de IA elegido por la persona (por defecto Mistral, UE; o su propio proveedor). Se presentan al modelo como *datos*, no como instrucciones.
- **Derechos**: ver (lista en Ajustes), rectificar/suprimir (borrar nota a nota o todo), portabilidad (`GET /api/me/datos`, JSON) y retirar el consentimiento.

## Notificaciones push (4.3)
- **Web Push con VAPID**. La clave VAPID solo identifica el sitio (`https://radio.guildawork.com`), nunca a una persona. El contenido va cifrado de extremo a extremo hasta el navegador.
- **El servicio de push** (Google FCM, Mozilla, Apple…) conoce el identificador del navegador y que se envía algo; no ve el contenido. Solo se aceptan endpoints de esos servicios (evita SSRF).
- **Mínimo dato**: ubicación redondeada a ~1 km (la de «Mi ubicación» al guardar), nivel de aviso y qué quiere recibir. Máximo 5 dispositivos por cuenta.
- **Sin entregas durante 90 días** → se borra el dispositivo; si el navegador da error 404/410 (baja), se borra al instante.

## Ejercicio de derechos
- **Acceso y portabilidad**: Ajustes → Memoria → «Descargar todos mis datos» (`/api/me/datos`). No incluye las claves de API.
- **Supresión**: Ajustes → Cuenta → «Borrar mis ajustes y claves» (`DELETE /api/me`) elimina ajustes, claves, memoria, dispositivos de notificaciones, alertas y rutas guardadas.
- **Retirada del consentimiento**: desactivar la memoria / las notificaciones.
- Cualquier otra solicitud: a la persona responsable de Guilda Work.
