import logging
import re
from decimal import Decimal, InvalidOperation

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)
from .config import TELEGRAM_BOT_TOKEN, ADMIN_TELEGRAM_IDS
from .chariow_api import create_checkout, ChariowError, list_products, get_sale
from .store import init_db, create_order, recent_orders, recent_sales

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("chariow-bot")

ASK_EMAIL, ASK_FIRST_NAME, ASK_LAST_NAME, ASK_COUNTRY, ASK_ADDRESS, ASK_CITY, ASK_STATE, ASK_ZIP, ASK_PHONE = range(9)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_TELEGRAM_IDS


def _integer_amount(value):
    """Retourne un montant entier fini, sinon None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _price_info(value):
    """Normalise les champs de prix renvoyés par Chariow."""
    if isinstance(value, dict):
        amount = value.get("value", value.get("amount"))
        currency = value.get("currency")
        formatted = value.get("formatted")
        try:
            numeric = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            numeric = None
        display = str(formatted) if formatted else (
            f"{amount} {currency}".strip() if amount is not None else None
        )
        return numeric, currency, display

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), None, str(value)

    if isinstance(value, str) and value.strip():
        return None, None, value.strip()

    return None, None, None


def format_product_pricing(product):
    """Formate le prix et indique une promotion si elle est vérifiable."""
    pricing = product.get("pricing") or {}
    base_raw = pricing.get("price")

    effective_raw = (
        pricing.get("effective")
        or pricing.get("current_price")
        or pricing.get("sale_price")
        or base_raw
        or product.get("price")
    )

    base, base_currency, base_display = _price_info(base_raw)
    effective, effective_currency, effective_display = _price_info(effective_raw)
    currency = effective_currency or base_currency

    if not effective_display:
        return "Prix non communiqué par Chariow"

    if base is not None and effective is not None and base > effective > 0:
        discount = round((base - effective) * 100 / base)
        if not base_display:
            base_display = f"{base:g} {base_currency or currency or ''}".strip()
        if not effective_currency and currency:
            effective_display = f"{effective_display} {currency}"

        return (
            f"Prix initial : {base_display}\n"
            f"🔥 Prix promotionnel : {effective_display}\n"
            f"🎉 Réduction : -{discount}%"
        )

    if not effective_currency and currency:
        effective_display = f"{effective_display} {currency}"

    return f"💰 Prix : {effective_display}"


def main_menu(admin: bool):
    rows = [
        [InlineKeyboardButton("🛍️ Voir le produit", callback_data="product")],
        [InlineKeyboardButton("💳 Acheter", callback_data="buy")],
        [InlineKeyboardButton("📦 Mes commandes", callback_data="orders")],
        [InlineKeyboardButton("🧰 Outils disponibles", callback_data="tools")],
    ]
    if admin:
        rows.append([InlineKeyboardButton("🛠️ Administration", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.effective_message.reply_text(
        "Bienvenue dans Chariow Bot.\n\nChoisis une action ci-dessous. "
        "Le paiement doit être confirmé par Chariow.",
        reply_markup=main_menu(is_admin(user.id)),
    )


async def products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    message = query.message if query else update.effective_message

    try:
        catalogue = await list_products()
    except ChariowError:
        log.warning("Impossible de récupérer le catalogue Chariow.")
        await message.reply_text(
            "Catalogue indisponible pour le moment. Réessaie plus tard."
        )
        return

    if not catalogue:
        await message.reply_text("Aucun produit n'a été renvoyé par Chariow.")
        return

    await message.reply_text("🛍️ Catalogue Chariow")

    shown = 0
    for product in catalogue:
        product_id = product.get("id") or product.get("product_id")
        if not isinstance(product_id, (str, int)):
            continue

        product_id = str(product_id)
        # Telegram limite callback_data à 64 octets.
        callback_data = f"buyprod:{product_id}"
        if len(callback_data.encode("utf-8")) > 64:
            log.warning("Produit ignoré : identifiant trop long pour Telegram.")
            continue

        name = (
            product.get("name")
            or product.get("title")
            or product.get("product_name")
            or "Produit sans nom"
        )
        price_text = format_product_pricing(product)

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🛒 Acheter ce produit",
                callback_data=callback_data,
            )
        ]])

        await message.reply_text(
            f"📦 {name}\n💰 Prix : {price_text}",
            reply_markup=keyboard,
        )
        shown += 1

    if shown == 0:
        await message.reply_text(
            "Aucun produit avec un identifiant compatible n'a été trouvé."
        )


async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    message = query.message if query else update.effective_message

    if not query or not isinstance(query.data, str) or not query.data.startswith("buyprod:"):
        await message.reply_text(
            "Pour acheter, ouvre /products et sélectionne le produit souhaité."
        )
        return ConversationHandler.END

    await query.answer()
    selected_id = query.data[len("buyprod:"):].strip()
    if not selected_id:
        await message.reply_text("Identifiant produit invalide. Rouvre /products.")
        return ConversationHandler.END

    try:
        catalogue = await list_products()
    except ChariowError:
        await message.reply_text(
            "Impossible de vérifier le catalogue Chariow. Réessaie plus tard."
        )
        return ConversationHandler.END

    selected = next(
        (item for item in catalogue
         if str(item.get("id") or item.get("product_id") or "") == selected_id),
        None,
    )
    if selected is None:
        await message.reply_text(
            "Produit indisponible. Rouvre /products et choisis un produit actuel."
        )
        return ConversationHandler.END

    product_name = str(
        selected.get("name") or selected.get("title")
        or selected.get("product_name") or "Produit"
    )
    pricing = selected.get("pricing") or {}
    price = (
        pricing.get("effective")
        or pricing.get("current_price")
        or pricing.get("sale_price")
        or pricing.get("price")
        or selected.get("price")
    )
    product_price, product_currency, _ = _price_info(price)
    product_currency = (
        product_currency or pricing.get("currency") or selected.get("currency")
    )

    if (
        product_price is None or product_price <= 0
        or not isinstance(product_currency, str)
        or not product_currency.strip()
    ):
        await message.reply_text(
            "Achat bloqué : le prix ou la devise ne peut pas être vérifié."
        )
        return ConversationHandler.END

    product_currency = product_currency.strip().upper()
    product_price_display = format_product_pricing(selected)
    context.user_data["checkout_form"] = {
        "product_id": selected_id,
        "product_name": product_name,
        "product_price_value": product_price,
        "product_currency": product_currency,
    }

    await message.reply_text(
        f"Produit choisi : {product_name}\n"
        f"Prix catalogue : {product_price_display}\n\n"
        "Étape 1/9 — Entre ton adresse e-mail."
    )
    return ASK_EMAIL


async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.effective_message.text or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        await update.effective_message.reply_text(
            "Adresse e-mail invalide. Réessaie, ou tape /cancel pour annuler."
        )
        return ASK_EMAIL
    context.user_data["checkout_form"]["email"] = value
    await update.effective_message.reply_text("Étape 2/9 — Quel est ton prénom ?")
    return ASK_FIRST_NAME


async def receive_first_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.effective_message.text or "").strip()
    if not value or len(value) > 100:
        await update.effective_message.reply_text("Prénom invalide. Réessaie ou tape /cancel.")
        return ASK_FIRST_NAME
    context.user_data["checkout_form"]["first_name"] = value
    await update.effective_message.reply_text("Étape 3/9 — Quel est ton nom de famille ?")
    return ASK_LAST_NAME


async def receive_last_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.effective_message.text or "").strip()
    if not value or len(value) > 100:
        await update.effective_message.reply_text("Nom invalide. Réessaie ou tape /cancel.")
        return ASK_LAST_NAME
    context.user_data["checkout_form"]["last_name"] = value
    await update.effective_message.reply_text(
        "Étape 4/9 — Indique le code pays ISO à deux lettres : CD pour la RDC, "
        "FR pour la France, BE pour la Belgique, etc."
    )
    return ASK_COUNTRY


async def receive_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.effective_message.text or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", value):
        await update.effective_message.reply_text(
            "Code invalide. Entre deux lettres, par exemple CD ou FR."
        )
        return ASK_COUNTRY
    context.user_data["checkout_form"]["country_code"] = value
    await update.effective_message.reply_text(
        "Étape 5/9 — Entre ton adresse complète (rue et numéro)."
    )
    return ASK_ADDRESS


async def receive_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.effective_message.text or "").strip()
    if not value or len(value) > 200:
        await update.effective_message.reply_text("Adresse invalide. Réessaie.")
        return ASK_ADDRESS
    context.user_data["checkout_form"]["address"] = value
    await update.effective_message.reply_text("Étape 6/9 — Dans quelle ville habites-tu ?")
    return ASK_CITY


async def receive_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.effective_message.text or "").strip()
    if not value or len(value) > 100:
        await update.effective_message.reply_text("Ville invalide. Réessaie.")
        return ASK_CITY
    context.user_data["checkout_form"]["city"] = value
    await update.effective_message.reply_text(
        "Étape 7/9 — Indique ta province, région ou ton État."
    )
    return ASK_STATE


async def receive_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.effective_message.text or "").strip()
    if not value or len(value) > 100:
        await update.effective_message.reply_text(
            "Province ou région invalide. Réessaie."
        )
        return ASK_STATE
    context.user_data["checkout_form"]["state"] = value
    await update.effective_message.reply_text(
        "Étape 8/9 — Entre ton code postal réel. Si ton adresse n'en possède pas, "
        "vérifie auprès de Chariow la valeur acceptée."
    )
    return ASK_ZIP


async def receive_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.effective_message.text or "").strip()
    if not value or len(value) > 20:
        await update.effective_message.reply_text(
            "Code postal invalide. Réessaie."
        )
        return ASK_ZIP
    context.user_data["checkout_form"]["zip"] = value
    await update.effective_message.reply_text(
        "Étape 9/9 — Entre ton numéro de téléphone sans indicatif international, "
        "avec les chiffres uniquement. Exemple : 812345678."
    )
    return ASK_PHONE


async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = re.sub(r"[\\s().-]", "", (update.effective_message.text or "").strip())
    if phone.startswith("+"):
        phone = phone[1:]
    if not phone.isdigit() or not 6 <= len(phone) <= 15:
        await update.effective_message.reply_text(
            "Numéro invalide. Entre 6 à 15 chiffres, sans indicatif international."
        )
        return ASK_PHONE

    form = context.user_data.get("checkout_form", {})
    if not all(form.get(k) for k in (
        "email", "first_name", "last_name", "country_code",
        "address", "city", "state", "zip"
    )):
        await update.effective_message.reply_text(
            "Formulaire incomplet. Relance /buy."
        )
        context.user_data.pop("checkout_form", None)
        return ConversationHandler.END

    await update.effective_message.reply_text(
        "Informations reçues. Je demande maintenant à Chariow de créer le checkout…"
    )
    try:
        result = await create_checkout(
            update.effective_user.id,
            email=form["email"],
            first_name=form["first_name"],
            last_name=form["last_name"],
            phone=phone,
            country_code=form["country_code"],
            address=form["address"],
            city=form["city"],
            state=form["state"],
            zip_code=form["zip"],
            product_id=form["product_id"],
        )
    except ChariowError as exc:
        log.warning("Chariow checkout refusé : %s", str(exc)[:300])
        await update.effective_message.reply_text(
            f"Paiement indisponible : {exc}\nAucun paiement confirmé."
        )
        context.user_data.pop("checkout_form", None)
        return ConversationHandler.END
    except Exception:
        log.exception("Erreur inattendue pendant la création du checkout")
        await update.effective_message.reply_text(
            "Erreur technique pendant la création du paiement. Réessaie plus tard."
        )
        context.user_data.pop("checkout_form", None)
        return ConversationHandler.END

    context.user_data.pop("checkout_form", None)
    step = result.get("step")
    purchase = result.get("purchase") or {}

    if step == "already_purchased":
        await update.effective_message.reply_text(
            "Chariow indique que ce produit a déjà été acheté. Aucune nouvelle commande créée."
        )
        return ConversationHandler.END

    if step == "completed":
        await update.effective_message.reply_text(
            "Chariow indique que l'achat est terminé. Une vérification supplémentaire "
            "est nécessaire avant toute livraison."
        )
        return ConversationHandler.END

    url = result.get("checkout_url")
    sale_id = purchase.get("id")
    if not url or not sale_id:
        await update.effective_message.reply_text(
            "Réponse Chariow incomplète. Aucune commande locale n'a été créée."
        )
        return ConversationHandler.END

    # N'enregistre que le montant explicitement renvoyé par Chariow.
    # L'absence de montant ne doit pas être remplacée par un prix supposé.
    amount_raw = purchase.get("amount")
    currency_raw = purchase.get("currency")
    if isinstance(amount_raw, dict):
        if not currency_raw:
            currency_raw = amount_raw.get("currency")
        amount_raw = amount_raw.get("value", amount_raw.get("amount"))

    amount_value = _integer_amount(amount_raw)
    if amount_raw is not None and amount_value is None:
        log.warning(
            "Montant Chariow absent, invalide ou non entier : "
            "vérification de vente maintenue en échec fermé."
        )

    currency_value = (
        str(currency_raw).strip().upper()
        if isinstance(currency_raw, str) and currency_raw.strip()
        else None
    )

    try:
        order_product_id = form["product_id"]
        create_order(
            update.effective_user.id, order_product_id, sale_id,
            url, currency_value, amount_value,
        )
    except Exception:
        log.exception("Impossible d'enregistrer le checkout local")
        await update.effective_message.reply_text(
            "Chariow a peut-être créé le checkout, mais son enregistrement local a échoué. "
            "Contacte l'administrateur avant de recommencer."
        )
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Ouvrir le paiement Chariow", url=url)
    ]])
    await update.effective_message.reply_text(
        "Checkout créé chez Chariow. Le paiement s'ouvre via le lien fourni par Chariow. "
        "Attends une confirmation vérifiée après le paiement.",
        reply_markup=keyboard,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("checkout_form", None)
    await update.effective_message.reply_text("Achat annulé. Aucune nouvelle commande demandée.")
    return ConversationHandler.END


async def orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = recent_orders(update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text("Aucune commande enregistrée pour le moment.")
        return
    lines = ["Tes dernières commandes (pending ne signifie pas payé) :"]
    for row in rows:
        lines.append(
            f"#{row['id']} — {row['status']} — {row['amount']} {row['currency']} "
            f"— vente Chariow: {row['chariow_sale_id'] or 'en attente'}"
        )
    await update.effective_message.reply_text("\n".join(lines))


async def verifier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Consulte en lecture seule une vente liée au compte Telegram."""
    user_id = update.effective_user.id
    rows = recent_orders(user_id, limit=100)

    requested_id = context.args[0].strip() if context.args else None
    if len(context.args) > 1:
        await update.effective_message.reply_text(
            "Utilisation : /verifier ou /verifier IDENTIFIANT_VENTE"
        )
        return

    if requested_id:
        row = next(
            (r for r in rows if r["chariow_sale_id"] == requested_id),
            None,
        )
    else:
        row = next(
            (r for r in rows if r["chariow_sale_id"]),
            None,
        )

    if row is None:
        await update.effective_message.reply_text(
            "Aucune vente correspondante n'est associée à ton compte Telegram. "
            "Utilise /orders pour consulter tes commandes."
        )
        return

    sale_id = row["chariow_sale_id"]
    await update.effective_message.reply_text(
        "Vérification en lecture seule auprès de Chariow…"
    )

    try:
        sale = await get_sale(sale_id)
    except ChariowError as exc:
        await update.effective_message.reply_text(
            f"Impossible de vérifier la vente : {exc}"
        )
        return
    except Exception:
        log.exception("Erreur inattendue pendant la vérification Chariow")
        await update.effective_message.reply_text(
            "Erreur inattendue pendant la consultation. Réessaie plus tard."
        )
        return

    sale_product = sale.get("product")
    sale_product_id = (
        sale_product.get("id") if isinstance(sale_product, dict) else None
    )
    amount_data = sale.get("amount")
    payment_data = sale.get("payment")

    amount_value = None
    currency_value = None
    if isinstance(amount_data, dict):
        amount_value = amount_data.get("formatted")
        if not amount_value:
            amount_value = amount_data.get("value")
        currency_value = amount_data.get("currency")

    if not currency_value and isinstance(payment_data, dict):
        payment_amount = payment_data.get("amount")
        if isinstance(payment_amount, dict):
            currency_value = payment_amount.get("currency")

    id_matches = sale.get("id") == sale_id
    product_matches = (
        isinstance(sale_product_id, str)
        and sale_product_id == row["product_id"]
    )

    provider_amount = (
        amount_data.get("value")
        if isinstance(amount_data, dict)
        else amount_data
    )

    # Un contrôle absent ou illisible échoue fermé : il n'est jamais
    # considéré comme une correspondance par défaut.
    amount_matches = False
    if row["amount"] is not None and provider_amount is not None:
        try:
            provider_decimal = Decimal(str(provider_amount))
            local_decimal = Decimal(str(row["amount"]))
            amount_matches = (
                provider_decimal.is_finite()
                and local_decimal.is_finite()
                and provider_decimal == local_decimal
            )
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            amount_matches = False

    currency_matches = False
    if row["currency"] and currency_value:
        currency_matches = (
            str(currency_value).strip().upper()
            == str(row["currency"]).strip().upper()
        )

    checks_ok = (
        id_matches and product_matches and amount_matches and currency_matches
    )

    payment_status = (
        payment_data.get("status", "inconnu")
        if isinstance(payment_data, dict) else "inconnu"
    )
    sale_status = sale.get("status", "inconnu")

    sale_status_norm = str(sale_status).strip().lower()
    payment_status_norm = str(payment_status).strip().lower()

    if sale_status_norm in {"abandoned", "failed", "cancelled", "canceled"} or payment_status_norm in {"cancelled", "canceled", "failed", "declined"}:
        payment_notice = "⛔ Vente abandonnée/échouée ou paiement annulé : ne pas livrer."
    elif checks_ok and sale_status_norm == "completed" and payment_status_norm in {"paid", "succeeded", "successful", "completed"}:
        payment_notice = "⚠️ Statuts compatibles avec un paiement réussi ; validation métier supplémentaire requise avant toute livraison."
    else:
        payment_notice = "⚠️ Paiement non confirmé par cette vérification. Ne pas livrer."

    lines = [
        "🔎 Résultat de vérification Chariow",
        f"Vente : {sale_id}",
        f"Statut de vente : {sale_status}",
        f"Statut du paiement : {payment_status}",
        payment_notice,
        f"Produit correspondant : {'oui' if product_matches else 'NON'}",
        f"Identifiant correspondant : {'oui' if id_matches else 'NON'}",
        f"Montant Chariow : {amount_value if amount_value is not None else 'indisponible'}",
        f"Devise : {currency_value or 'indisponible'}",
        f"Montant local cohérent : {'oui' if amount_matches else 'NON'}",
        f"Devise locale cohérente : {'oui' if currency_matches else 'NON'}",
        f"Contrôles de cohérence : {'OK' if checks_ok else 'ÉCHEC'}",
        "",
        "Lecture seule : aucun statut local modifié et aucune livraison déclenchée.",
        "Ce résultat n'active pas la livraison automatique.",
    ]
    await update.effective_message.reply_text("\n".join(lines))


async def tools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🧰 Outils disponibles\n/start — menu\n/products — produit\n/buy — acheter\n/orders — mes commandes\n/verifier — vérifier une vente\n/tools — cette liste\n/cancel — annuler un achat"
    if is_admin(update.effective_user.id):
        text += "\n/admin — tableau de bord admin\n/payuser — informations sur les versements sortants"
    await update.effective_message.reply_text(text)


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Accès refusé.")
        return
    rows = recent_sales()
    lines = [
        f"🛠️ Administration\nCommandes enregistrées : {len(rows)}",
        "⚠️ Les états locaux ne prouvent pas qu'un paiement a été reçu.",
    ]
    for row in rows[:20]:
        lines.append(
            f"#{row['id']} user={row['telegram_user_id']} status={row['status']} "
            f"amount={row['amount']} {row['currency']} sale={row['chariow_sale_id']}"
        )
    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🧰 Outils", callback_data="tools")],
            [InlineKeyboardButton("💸 Paiement vers un utilisateur", callback_data="payuser")],
        ]),
    )


async def payuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Accès refusé.")
        return
    await update.effective_message.reply_text(
        "Les versements sortants ne sont pas activés : aucun endpoint officiel Chariow "
        "de paiement sortant n'a été vérifié. Aucun transfert ne sera simulé."
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "product":
        await products(update, context)
    elif q.data == "tools":
        await tools(update, context)
    elif q.data == "admin":
        await admin(update, context)
    elif q.data == "payuser":
        await payuser(update, context)
    elif q.data == "orders":
        await orders(update, context)


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await buy_start(update, context)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await admin(update, context)


async def cmd_payuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await payuser(update, context)


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant. Configure le secret dans .env.")
    init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("buy", cmd_buy),
            CallbackQueryHandler(buy_start, pattern="^buy$"),
            CallbackQueryHandler(buy_start, pattern="^buyprod:"),
        ],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email)],
            ASK_FIRST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_first_name)],
            ASK_LAST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_last_name)],
            ASK_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_country)],
            ASK_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_address)],
            ASK_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_city)],
            ASK_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_state)],
            ASK_ZIP: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_zip)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("products", products))
    app.add_handler(CommandHandler("orders", orders))
    app.add_handler(CommandHandler("verifier", verifier))
    app.add_handler(CommandHandler("tools", tools))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("payuser", cmd_payuser))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(callbacks))
    log.info("Chariow Bot démarré; aucune confirmation de vente n'est activée sans Pulse vérifié.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
