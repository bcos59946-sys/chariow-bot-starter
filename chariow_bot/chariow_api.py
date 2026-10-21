import httpx
from .config import CHARIOW_API_BASE, CHARIOW_API_KEY

class ChariowError(RuntimeError):
    pass

async def list_products() -> list[dict]:
    """Récupère le catalogue Chariow avec pagination."""
    headers = {"Authorization": f"Bearer {CHARIOW_API_KEY}"}
    products = []
    cursor = None

    async with httpx.AsyncClient(timeout=20.0) as client:
        for _ in range(10):
            params = {"per_page": 20}
            if cursor:
                params["cursor"] = cursor

            try:
                response = await client.get(
                    f"{CHARIOW_API_BASE}/products",
                    headers=headers,
                    params=params,
                )
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ChariowError(
                    "Impossible de récupérer le catalogue Chariow."
                ) from exc

            data = body.get("data", [])
            pagination = body.get("pagination", {}) or {}

            if isinstance(data, dict):
                pagination = data.get("pagination", pagination) or {}
                data = data.get("data", [])

            if not isinstance(data, list):
                raise ChariowError("Format de catalogue inattendu.")

            products.extend(
                item for item in data if isinstance(item, dict)
            )

            if not (pagination.get("has_more") or pagination.get("has_more_pages")):
                break

            next_cursor = pagination.get("next_cursor")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

    return products

async def create_checkout(
    user_id: int,
    email: str,
    first_name: str,
    last_name: str,
    phone: str,
    country_code: str,
    address: str,
    city: str,
    state: str,
    zip_code: str,
    product_id: str,
) -> dict:
    if not CHARIOW_API_KEY:
        raise ChariowError("CHARIOW_API_KEY n'est pas configurée.")
    if not isinstance(product_id, str) or not product_id.strip():
        raise ChariowError("Aucun produit valide sélectionné.")


    # Les champs client sont transmis explicitement à Chariow.
    payload = {
        "product_id": product_id.strip(),
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "shipping": {
            "address": address,
            "city": city,
            "state": state,
            "country": country_code.upper(),
            "zip": zip_code,
        },
        "phone": {
            "number": phone,
            "country_code": country_code.upper(),
        },
        "custom_metadata": {
            "source": "telegram_bot",
            "telegram_user_id": str(user_id),
        },
    }

    headers = {"Authorization": f"Bearer {CHARIOW_API_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{CHARIOW_API_BASE}/checkout", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise ChariowError("Impossible de joindre Chariow pour le moment.") from exc
    if response.status_code >= 400:
        # Ne jamais afficher la clé API ni le corps brut de la réponse.
        try:
            error_data = response.json()
        except ValueError:
            error_data = {}

        message = error_data.get("message")
        errors = error_data.get("errors")
        details = []

        if isinstance(errors, dict):
            for field, messages in errors.items():
                if not isinstance(field, str):
                    continue
                if isinstance(messages, list):
                    safe_messages = [
                        str(item)[:180]
                        for item in messages
                        if isinstance(item, str)
                    ]
                    details.extend(
                        f"{field}: {msg}" for msg in safe_messages
                    )
                elif isinstance(messages, str):
                    details.append(f"{field}: {messages[:180]}")
                else:
                    details.append(field)
        elif isinstance(errors, list):
            # Ne pas afficher le contenu brut des erreurs inconnues.
            details = ["erreur_api"]

        summary = ", ".join(details[:8]) if details else "détails non fournis"
        safe_message = (
            message[:180]
            if isinstance(message, str)
            and not any(secret in message.lower() for secret in
                        ("bearer", "token", "secret", "authorization", "api_key"))
            else "Validation ou paramètres refusés"
        )

        raise ChariowError(
            f"Chariow HTTP {response.status_code} : {safe_message}. "
            f"Champs signalés : {summary}."
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise ChariowError("Réponse Chariow illisible.") from exc

    if not isinstance(body, dict):
        raise ChariowError("Format de réponse Chariow inattendu.")

    data = body.get("data")
    if not isinstance(data, dict):
        raise ChariowError("Réponse Chariow sans objet data exploitable.")

    step = data.get("step")
    purchase = data.get("purchase") or {}
    if not isinstance(purchase, dict):
        purchase = {}

    if step == "already_purchased":
        return {"step": step, "purchase": purchase}

    if step == "completed":
        return {"step": step, "purchase": purchase, "checkout_url": None}

    checkout = data.get("payment") or {}
    if not isinstance(checkout, dict):
        checkout = {}

    url = checkout.get("checkout_url")
    sale_id = purchase.get("id")

    if step != "payment" or not isinstance(url, str) or not url.startswith("https://") or not sale_id:
        raise ChariowError("Réponse Chariow incomplète : vérification du checkout nécessaire.")

    return {"step": step, "purchase": purchase, "checkout_url": url}


async def get_sale(sale_id: str) -> dict:
    """Consulte une vente Chariow sans modifier son état."""
    if not CHARIOW_API_KEY:
        raise ChariowError("CHARIOW_API_KEY n'est pas configurée.")

    if not isinstance(sale_id, str) or not sale_id.strip():
        raise ChariowError("Identifiant de vente invalide.")

    headers = {
        "Authorization": f"Bearer {CHARIOW_API_KEY}",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{CHARIOW_API_BASE}/sales/{sale_id.strip()}",
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 401 or code == 403:
            raise ChariowError(
                "Accès refusé par Chariow : vérifie la clé et ses permissions."
            ) from None
        if code == 404:
            raise ChariowError(
                "Vente introuvable dans Chariow."
            ) from None
        raise ChariowError(
            f"Consultation Chariow impossible (HTTP {code})."
        ) from None
    except (httpx.HTTPError, ValueError) as exc:
        raise ChariowError(
            "Impossible de consulter ou de lire la vente Chariow."
        ) from exc

    if not isinstance(body, dict):
        raise ChariowError("Format de réponse Chariow inattendu.")

    sale = body.get("data")
    if not isinstance(sale, dict):
        raise ChariowError("Réponse Chariow sans données de vente exploitables.")

    return sale
