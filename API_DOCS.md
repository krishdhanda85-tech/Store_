# Reseller API Documentation

Buy digital products from our store automatically. Every reply is JSON.

## 1. Quick start

Open `https://YOUR-DOMAIN/api/docs` for the redesigned interactive developer portal and a safe header-based connection test. The API key is sent in a request header there, not placed in the URL.

1. Open the Telegram bot → **Reseller API** → create your API key (shown only once, copy it).
2. Top up your wallet with **Add Funds** in the bot. Orders are paid from this wallet.
3. Test in your browser:

```
https://YOUR-DOMAIN/api/v1/index.php?action=balance&api_key=YOUR_KEY
```

If you see `{"ok":true,"balance":...}`, you are connected. If you see `missing_api_key`, the request did not include the key in a supported header or parameter. If you see `invalid_api_key`, the key was received but is not active in this shop database.

**API URL (paste into your panel URL field):** `https://YOUR-DOMAIN/api/v1/index.php`

Put the key in the panel’s separate **API Key** field. Do not append it to the endpoint URL unless the panel specifically requires query-string authentication.

## 2. Authentication

Send your key in any one of these ways:

| Method | Example |
|---|---|
| Bearer header | `Authorization: Bearer YOUR_KEY` |
| Plain header | `Authorization: YOUR_KEY` |
| API key header | `X-API-Key: YOUR_KEY` |
| Query / form / JSON field | `api_key=YOUR_KEY` (also `key`, `token`) |

Keys look like `rs_live_...`. Never share your key. If it leaks, regenerate it in the bot (the old key stops working immediately).

## 3. Easy mode (one URL)

`GET` or `POST` to `/api/v1/index.php` (also `/api/v1`) with an `action` parameter.

| action | Aliases | Parameters | What it does |
|---|---|---|---|
| `products` | `services`, `list`, `stock`, `items` | none | Products, price, stock |
| `balance` | `profile`, `me` | none | Wallet balance |
| `buy` | `create_order`, `purchase`, `add` | `product_id`, `quantity`, optional `customer_ref`, `idempotency_key` | Place an order |
| `order` | `status`, `get_order`, `check` | `order_id` | One order and its items |
| `orders` | `history` | none | Your last 100 orders |
| `health` | | none | Server check (no key needed) |

Unknown action → `400 unknown_action`.

## 4. REST mode (same features)

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health` | Server check, no key needed |
| GET | `/api/v1/balance` | Wallet balance |
| GET | `/api/v1/products` | Products, prices, stock |
| POST | `/api/v1/orders` | Create order |
| GET | `/api/v1/orders` | Last 100 orders |
| GET | `/api/v1/orders/{order_id}` | One order |

## 5. Responses

### Products
```json
{
  "ok": true,
  "currency": "shop_balance_units",
  "products": [
    {"id": 1, "name": "Netflix", "description": "...", "category": "...",
     "kind": "auto", "price": 2.0, "stock": 3, "available": true}
  ]
}
```
`price` is your price (your discount already applied). Only `auto` (instant delivery) products can be bought through the API.

### Balance
```json
{"ok": true, "reseller_id": 1, "balance": 10.0}
```

### Create order
Request:
```
POST /api/v1/index.php
action=buy&product_id=1&quantity=2&idempotency_key=order-1001&api_key=YOUR_KEY
```
or REST:
```
POST /api/v1/orders
Authorization: Bearer YOUR_KEY
Idempotency-Key: order-1001
Content-Type: application/json

{"product_id": 1, "quantity": 2, "customer_ref": "buyer-55"}
```
Reply `201`:
```json
{
  "ok": true,
  "order": {
    "id": "RSL-1A2B3C4D5E6F", "product_id": 1, "product_name": "Netflix",
    "quantity": 2, "unit_price": 2.0, "amount": 4.0, "status": "paid",
    "items": ["a@x:1", "b@x:2"], "customer_ref": "buyer-55",
    "created_at": "2026-10-18T12:27:20+00:00"
  },
  "balance": 6.0
}
```
`items` are the delivered accounts/links. Max quantity per order: **20**.

### Idempotency (important)
Send a unique `Idempotency-Key` (header) or `idempotency_key` (field) with every order. If your request times out and you retry with the same key, you get the original order back with `"replayed": true` and you are **not charged twice**.

### Order status
```json
{"ok": true, "order": {"id": "RSL-...", "status": "paid", "items": ["..."], "...": "..."}}
```

## 6. Errors

Errors always look like `{"ok": false, "error": "code"}`.

| HTTP | error | Meaning |
|---|---|---|
| 400 | `product_id_and_quantity_must_be_integers` | Bad input |
| 400 | `quantity_must_be_between_1_and_20` | Quantity out of range |
| 400 | `unknown_action` | Wrong `action` |
| 401 | `missing_api_key` / `invalid_api_key` | No key or wrong key |
| 402 | `insufficient_balance` | Wallet too low (reply includes `required` and `balance`) |
| 403 | `reseller_disabled` | Your access is disabled, contact support |
| 404 | `product_not_found` / `order_not_found` / `not_found` | Not found or wrong URL |
| 405 | `method_not_allowed` | Wrong HTTP method |
| 409 | `insufficient_stock` | Not enough stock |
| 409 | `manual_products_not_supported_by_api` | Product is not instant-delivery |
| 500 | `server_error` | Try again; contact support if it continues |

## 7. Examples

**curl**
```bash
curl "https://YOUR-DOMAIN/api/v1/index.php?action=products" -H "Authorization: Bearer YOUR_KEY"

curl -X POST "https://YOUR-DOMAIN/api/v1/orders" \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Idempotency-Key: order-1001" \
  -H "Content-Type: application/json" \
  -d '{"product_id":1,"quantity":2}'
```

**Python**
```python
import requests
BASE = "https://YOUR-DOMAIN/api/v1/index.php"
KEY = "YOUR_KEY"

print(requests.get(BASE, params={"action": "products", "api_key": KEY}).json())

r = requests.post(BASE, data={"action": "buy", "product_id": 1, "quantity": 2,
                              "idempotency_key": "order-1001", "api_key": KEY})
print(r.status_code, r.json())
```

**PHP**
```php
$ch = curl_init("https://YOUR-DOMAIN/api/v1/index.php");
curl_setopt_array($ch, [
  CURLOPT_POST => true,
  CURLOPT_RETURNTRANSFER => true,
  CURLOPT_POSTFIELDS => http_build_query([
    "action" => "buy", "product_id" => 1, "quantity" => 2,
    "idempotency_key" => "order-1001", "api_key" => "YOUR_KEY"
  ]),
]);
$res = json_decode(curl_exec($ch), true);
```

## 8. Panel setup (e.g. Goatsell)

- Supplier API Endpoint URL: `https://YOUR-DOMAIN/api/v1/index.php`
- Supplier API Key: your `rs_live_...` key
- Use `https://` (not `http://`) and the full path above, not only the domain.

## 9. Tips

- Check `balance` before big orders; top up through the bot's **Add Funds**.
- Cache `products` for a short time; stock changes with every sale.
- Always send an idempotency key on `buy`.

MADE-TO-ORDER OTP FLOW (NEW)
----------------------------
1. Seller opens the admin panel > Resellers API and taps “I’m Online”. Keep that page open; a heartbeat keeps the online state fresh. If the page is closed or stops heartbeating for 3 minutes, the API reports the seller offline.
2. The reseller bot should call GET /api/v1/seller-status with its API key. Show “Request OTP” only when online.
3. When the customer taps Request OTP, the reseller bot calls POST /api/v1/orders/ORDER_ID/request-otp with its API key. If seller is offline, the API returns HTTP 409 with error seller_offline.
4. The seller enters the current OTP in the website and taps “Send OTP to reseller API”. Order status becomes otp_ready. The reseller bot should poll GET /api/v1/orders/ORDER_ID; when status is otp_ready, send the `otp` field to the matching customer.
5. After the customer has used the OTP, the seller enters profile and PIN on the website and completes the order. Status becomes completed; the reseller bot polls the order and sends `profile` and `pin` to that same customer.

Order statuses for this flow: processing, otp_requested, otp_ready, completed. Use order ID plus your own customer mapping so data never goes to a different customer. API keys must remain private. OTP/PIN are sensitive; do not log them or store them in public channels. The website retains them in the shop database for the order record; remove the order record when no longer needed if your retention requirements call for it.
