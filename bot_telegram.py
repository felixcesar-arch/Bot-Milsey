"""
bot_telegram.py
Bot de Telegram para MILSEY (joyería en acero inoxidable y chapa en oro).
Versión final verificada para Google Sheets + GitHub/Render.
"""

import html
import io
import os
import re
import unicodedata

import httpx
import requests
import pandas as pd
from fastapi import FastAPI, Request


# --------------------------------------------------------------------------
# 1. CONFIGURACIÓN
# --------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL", "https://docs.google.com/...").strip()
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

MAX_RESULTADOS = 8

PALABRAS_VACIAS = {
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas",
    "y", "o", "en", "con", "para", "por", "quiero", "busco", "tienen",
    "tiene", "hay", "me", "mi", "que", "existencia", "existencias",
    "disponible", "disponibles", "stock", "precio", "precios", "cuanto",
    "cuesta", "cuestan", "hola", "buenas", "dias", "tardes", "noches",
    "vende", "venden", "buscando", "catalogo", "pieza", "piezas",
}

app = FastAPI(title="Milsey - Bot de Telegram")

# --------------------------------------------------------------------------
# 2. MENSAJES FIJOS
# --------------------------------------------------------------------------

MENSAJE_BIENVENIDA = (
    "✨ <b>Bienvenido/a a MILSEY</b> ✨\n\n"
    "Joyería en acero inoxidable y chapa en oro, pensada para brillar contigo "
    "todos los días.\n\n"
    "Para consultar nuestras piezas y existencias, escríbeme lo que buscas. "
    "Por ejemplo:\n"
    "• <i>anillos</i>\n"
    "• <i>collar</i>\n"
    "• <i>aretes</i>\n"
    "• <i>chapa de oro</i>\n\n"
    "Puedes buscar por nombre de la pieza, categoría o material. 💛"
)

MENSAJE_SIN_RESULTADOS = (
    "Por el momento no encontré esa pieza disponible en nuestro catálogo 🤍\n\n"
    "Prueba con otra palabra, como <i>anillos</i>, <i>collares</i>, <i>aretes</i> "
    "o <i>pulseras</i>."
)

MENSAJE_CONSULTA_VACIA = (
    "Cuéntame un poco más qué pieza buscas 💎\n"
    "Por ejemplo: <i>anillos</i>, <i>aretes</i> o <i>chapa de oro</i>."
)

MENSAJE_ERROR_ARCHIVO = (
    "En este momento no puedo consultar el catálogo 🙏\n"
    "Inténtalo de nuevo en unos minutos, por favor."
)

# --------------------------------------------------------------------------
# 3. UTILIDADES DE TEXTO Y BÚSQUEDA
# --------------------------------------------------------------------------


def normalizar(texto) -> str:
    """Pasa a minúsculas, quita acentos y espacios extra."""
    if texto is None or pd.isna(texto):
        return ""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto.lower()).strip()


def a_singular(palabra: str) -> str:
    """Convierte plurales comunes en español a singular."""
    if len(palabra) <= 3:
        return palabra
    if palabra.endswith(("as", "es", "is", "os", "us")):
        if palabra.endswith(("res", "les", "nes", "des")) and len(palabra) > 4:
            if not palabra.endswith(("tes", "ques", "gues")):
                return palabra[:-2]
        return palabra[:-1]
    return palabra


def extraer_terminos(consulta: str) -> list:
    """Extrae palabras clave limpias y en singular."""
    texto = re.sub(r"[^\w\s]", " ", normalizar(consulta))
    return [a_singular(t) for t in texto.split() if t not in PALABRAS_VACIAS]


def texto_celda(valor, defecto: str = "") -> str:
    if valor is None or pd.isna(valor):
        return defecto
    texto = str(valor).strip()
    return texto if texto else defecto


def limpiar_numero(valor):
    """Convierte precios o stocks con símbolos ($1,250.00) a float."""
    if valor is None or pd.isna(valor):
        return float("nan")
    limpio = re.sub(r"[^\d.-]", "", str(valor))
    return pd.to_numeric(limpio, errors="coerce")


def obtener_url_csv(url_sheet: str) -> str:
    """Convierte el link de Google Sheets en un link directo de exportación CSV."""
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url_sheet)
    if not match:
        raise ValueError("Falta pegar un enlace válido en GOOGLE_SHEET_URL.")
    sheet_id = match.group(1)
    gid_match = re.search(r"[#&?]gid=(\d+)", url_sheet)
    gid_param = f"&gid={gid_match.group(1)}" if gid_match else ""
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv{gid_param}"


# --------------------------------------------------------------------------
# 4. LECTURA DE GOOGLE SHEETS
# --------------------------------------------------------------------------


async def cargar_inventario() -> pd.DataFrame:
    """Descarga el CSV de Google Sheets de forma asíncrona con timeout seguro."""
    csv_url = obtener_url_csv(GOOGLE_SHEET_URL)

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as cliente:
        respuesta = await cliente.get(csv_url)
        respuesta.raise_for_status()

    contenido_csv = respuesta.content.decode("utf-8-sig")

    # Detecta si Google bloqueó la descarga por falta de permisos públicos
    if contenido_csv.lstrip().startswith(("<!DOCTYPE html>", "<html")):
        raise PermissionError(
            "Tu Google Sheet está como Restringido. "
            "Cámbialo a 'Cualquier persona con el enlace' en el botón Compartir."
        )

    df = pd.read_csv(io.StringIO(contenido_csv))
    df.columns = [normalizar(c) for c in df.columns]

    renombres = {
        "nombre_articulo": "producto",
        "nombre": "producto",
        "articulo": "producto",
        "existencias": "stock",
        "existencia": "stock",
        "id_producto": "id",
    }
    df = df.rename(columns=renombres)

    if "producto" not in df.columns:
        raise ValueError(f"No se halló la columna de producto. Columnas leídas: {list(df.columns)}")

    for columna in ("categoria", "material", "precio", "stock"):
        if columna not in df.columns:
            df[columna] = pd.NA

    df = df[df["producto"].notna() & (df["producto"].astype(str).str.strip() != "")]
    return df


def normalizar_frase(frase) -> str:
    """Normaliza todas las palabras de una celda también a singular."""
    limpia = normalizar(frase)
    palabras_singulares = [a_singular(p) for p in limpia.split()]
    return limpia + " " + " ".join(palabras_singulares)


def buscar_piezas(df: pd.DataFrame, consulta: str):
    terminos = extraer_terminos(consulta)
    if not terminos or df.empty:
        return df.iloc[0:0], False

    texto_fila = (
        df["producto"].map(normalizar_frase) + " "
        + df["categoria"].map(normalizar_frase) + " "
        + df["material"].map(normalizar_frase)
    )

    coincide_todos = texto_fila.apply(lambda t: all(term in t for term in terminos))
    if coincide_todos.any():
        return df[coincide_todos], True

    coincide_alguno = texto_fila.apply(lambda t: any(term in t for term in terminos))
    return df[coincide_alguno], False


# --------------------------------------------------------------------------
# 5. FORMATO DE LA RESPUESTA
# --------------------------------------------------------------------------


def formatear_pieza(fila: pd.Series) -> str:
    nombre = html.escape(texto_celda(fila.get("producto"), "Pieza sin nombre"))
    categoria = html.escape(texto_celda(fila.get("categoria"), "—"))
    material = html.escape(texto_celda(fila.get("material"), "—"))

    precio = limpiar_numero(fila.get("precio"))
    texto_precio = "Consultar precio" if pd.isna(precio) else f"${precio:,.2f} MXN"

    stock = limpiar_numero(fila.get("stock"))
    if pd.isna(stock):
        texto_stock = "Consultar disponibilidad"
    elif stock <= 0:
        texto_stock = "⚠️ Agotado por ahora"
    elif stock <= 3:
        texto_stock = f"✅ ¡Últimas {int(stock)} pieza(s)!"
    else:
        texto_stock = f"✅ Disponible ({int(stock)} piezas)"

    return (
        f"💎 <b>{nombre}</b>\n"
        f"• Categoría: {categoria}\n"
        f"• Material: {material}\n"
        f"• Precio: {texto_precio}\n"
        f"• {texto_stock}"
    )


async def consultar_inventario(consulta: str) -> str:
    if not extraer_terminos(consulta):
        return MENSAJE_CONSULTA_VACIA

    try:
        df = await cargar_inventario()
    except Exception as error:
        print(f"[ERROR] No se pudo leer Google Sheets: {error}")
        return MENSAJE_ERROR_ARCHIVO

    resultados, es_exacta = buscar_piezas(df, consulta)
    if resultados.empty:
        return MENSAJE_SIN_RESULTADOS

    total = len(resultados)
    fichas = [formatear_pieza(fila) for _, fila in resultados.head(MAX_RESULTADOS).iterrows()]

    if es_exacta:
        encabezado = "✨ <b>Esto es lo que encontré para ti:</b>\n\n"
    else:
        encabezado = (
            "No encontré exactamente eso, pero estas piezas podrían gustarte 🤍\n\n"
        )
    cuerpo = "\n\n".join(fichas)
    pie = ""
    if total > MAX_RESULTADOS:
        pie = f"\n\n<i>Mostrando {MAX_RESULTADOS} de {total} piezas. Afina tu búsqueda para ver más.</i>"

    return encabezado + cuerpo + pie


# --------------------------------------------------------------------------
# 6. ENVÍO A TELEGRAM
# --------------------------------------------------------------------------


async def enviar_mensaje(chat_id: int, texto: str) -> None:
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            respuesta = await cliente.post(TELEGRAM_API_URL, json=payload)
            respuesta.raise_for_status()
    except httpx.HTTPError as error:
        print(f"[ERROR] No se pudo enviar el mensaje al chat {chat_id}: {error}")


# --------------------------------------------------------------------------
# 7. ENDPOINTS
# --------------------------------------------------------------------------


@app.get("/")
async def comprobar_servidor():
    return {"status": "ok", "mensaje": "Servidor del bot de Milsey en línea ✨"}


@app.get("/probar-inventario")
async def probar_inventario():
    """Ruta de diagnóstico para verificar en el navegador que Google Sheets lee bien."""
    try:
        df = await cargar_inventario()
        return {
            "status": "ok",
            "piezas_cargadas": len(df),
            "muestra": df[["producto", "categoria", "precio", "stock"]].head(5).to_dict(orient="records"),
        }
    except Exception as error:
        return {"status": "error", "detalle": str(error)}


@app.post("/webhook")
async def recibir_actualizacion(request: Request):
    try:
        update = await request.json()
    except Exception:
        return {"status": "ok"}

    mensaje = update.get("message")
    if not isinstance(mensaje, dict):
        return {"status": "ok"}

    texto = mensaje.get("text")
    chat_id = mensaje.get("chat", {}).get("id")
    if not texto or chat_id is None:
        return {"status": "ok"}

    texto = texto.strip()

    try:
        # Si mandan comandos de inicio o ayuda, mandamos el mensaje de bienvenida fijo
        if texto.lower().startswith(("/start", "/help", "/ayuda")):
            await enviar_mensaje(chat_id, MENSAJE_BIENVENIDA)
            return {"status": "ok"}

        # 1. Buscamos las piezas relevantes en Google Sheets basándonos en lo que escribió el cliente
        df_inventario = await cargar_inventario()
        resultados, _ = buscar_piezas(df_inventario, texto)

        # 2. Preparamos un resumen del inventario encontrado para dárselo como contexto a la IA
        contexto_inventario = "No se encontraron piezas exactas en el inventario para esta consulta."
        if not resultados.empty:
            piezas_encontradas = []
            for _, fila in resultados.head(5).iterrows():
                p_nombre = texto_celda(fila.get("producto"))
                p_cat = texto_celda(fila.get("categoria"))
                p_mat = texto_celda(fila.get("material"))
                p_precio = limpiar_numero(fila.get("precio"))
                p_stock = limpiar_numero(fila.get("stock"))
                
                precio_txt = f"${p_precio:,.2f} MXN" if not pd.isna(p_precio) else "Precio por consultar"
                stock_txt = f"Disponible ({int(p_stock)} piezas)" if not pd.isna(p_stock) and p_stock > 0 else "Agotado"
                
                piezas_encontradas.append(f"- {p_nombre} | Categoría: {p_cat} | Material: {p_mat} | Precio: {precio_txt} | Estado: {stock_txt}")
            
            contexto_inventario = "Piezas disponibles en el catálogo de Milsey que coinciden con la búsqueda:\n" + "\n".join(piezas_encontradas)

        # 3. Consultamos a la IA de Groq pasándole tanto el mensaje del cliente como el inventario real
        respuesta_ia = consultar_ia_groq_con_inventario(texto, contexto_inventario)

    except Exception as error:
        print(f"[ERROR] Fallo procesando la consulta '{texto}': {error}")
        respuesta_ia = "¡Hola! Disculpa, tuve un pequeño detalle técnico consultando el catálogo. ¿En qué te puedo ayudar?"

    # 4. Enviamos la respuesta redactada por la IA al chat de Telegram
    await enviar_mensaje(chat_id, respuesta_ia)
    return {"status": "ok"}
    


    texto = texto.strip()

    try:
        if texto.lower().startswith(("/start", "/help", "/ayuda")):
            respuesta = MENSAJE_BIENVENIDA
        else:
            respuesta = await consultar_inventario(texto)
    except Exception as error:
        print(f"[ERROR] Fallo procesando la consulta '{texto}': {error}")
        respuesta = MENSAJE_ERROR_ARCHIVO

    await enviar_mensaje(chat_id, respuesta)
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("bot_telegram:app", host="0.0.0.0", port=port)


@app.on_event("startup")
def configurar_webhook():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    render_url = os.getenv("RENDER_EXTERNAL_URL") # Render la llena solita con tu URL
    
    if token and render_url:
        # Asegúrate de que '/webhook' coincida con la ruta exacta donde recibes los mensajes (ej. @app.post("/webhook"))
        webhook_url = f"{render_url}/webhook"
        url = f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}"
        
        try:
            respuesta = requests.get(url)
            print("Respuesta de Telegram al configurar webhook:", respuesta.json())
        except Exception as e:
            print("No se pudo configurar el webhook automáticamente:", e)

def consultar_ia_groq_con_inventario(mensaje_usuario, contexto_inventario):
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        return "Error: Falta configurar la variable GROQ_API_KEY en Render."
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {groq_api_key}",
        "Content-Type": "application/json"
    }
    
    prompt_sistema = (
        "Eres el asistente virtual experto y amable de Milsey, una joyería especializada en acero inoxidable y chapa en oro. "
        "Tu objetivo es atender a los clientes de forma cálida, servicial y vendedora. "
        "Utiliza la siguiente información del inventario real de la tienda para responder al cliente de manera natural, "
        "mencionando los nombres de las piezas, sus precios y materiales cuando sea relevante:\n\n"
        f"{contexto_inventario}"
    )

    data = {
        "model": "llama3-70b-8192",
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": mensaje_usuario}
        ],
        "temperature": 0.7
    }
    
    try:
        # Añadimos un timeout de 8 segundos para que no se quede colgado
        response = requests.post(url, json=data, headers=headers, timeout=8.0)
        
        # Si la API regresa un código de error (ej. 401 Unauthorized, 429 Rate Limit, etc.)
        if response.status_code != 200:
            print(f"⚠️ Groq respondió con código {response.status_code}: {response.text}")
            return f"Error HTTP {response.status_code} de Groq: {response.text[:100]}"
            
        resultado = response.json()
        
        if "error" in resultado:
            print("❌ Error en JSON de Groq:", resultado["error"])
            return f"Error Groq: {resultado['error'].get('message', 'Desconocido')}"
            
        return resultado["choices"][0]["message"]["content"]
        
    except Exception as e:
        # ESTO MANDARÁ EL ERROR EXACTO AL CHAT DE TELEGRAM PARA VERLO DE INMEDIATO
        error_str = repr(e)
        print("❌ EXCEPCIÓN CRÍTICA CON GROQ:", error_str)
        return f"Excepción técnica: {error_str}"
