# OEM Brand Request Contract

`brand_mark` is tenant identity, not presentation-only metadata. Every request must resolve to exactly one brand.

## Authority Rules

1. Public requests without a JWT must carry the brand through `X-Lobster-Brand`, `brand`/`brand_mark` query, or a typed body/form field.
2. If more than one explicit source is present, all values must match. Conflicting values return HTTP 400.
3. Authenticated requests use the signed JWT `brand_mark` as the authority. An explicit request brand is optional, but when supplied it must match both the JWT and the database user or the request returns HTTP 403.
4. Legacy JWTs without `brand_mark` are treated as `bihuo` only.
5. Background work inherits brand from its user/JWT. New Online builds also send `X-Lobster-Brand`; the server must still accept old builds that only carry the signed JWT.
6. User data remains scoped by `user_id`. Login identities, SMS challenges, OAuth identities, installation IDs, and mobile device IDs are additionally brand-scoped where they exist before authenticated user context.

## Client Coverage

| Client or transport | Brand propagation |
| --- | --- |
| H5 REST and uploads | `apiUrl()` query plus `authHeaders()` header |
| H5 SSE | `apiUrl()` query; JWT is also present |
| H5 voice WebSocket | `brand` query plus JWT |
| Admin | URL-selected brand, shared API header, login body |
| Mini Program REST | shared wrapper query, header, and body |
| Mini Program uploads | shared wrapper query, header, and form data |
| Online browser | global first-party API fetch interceptor plus login body |
| Online Python cloud calls | `with_oem_brand_header()` plus JWT where authenticated |
| Scheduled-task callbacks | installed brand header, installation ID, and JWT |

## Server Entry Points

- `resolve_request_brand_mark()` is the only resolver for public request header/query/body signals.
- `validate_token_brand()` is the shared authenticated request check.
- Non-standard token transports such as SSE query tokens, WebSockets, Messenger, and Comfly must call the same validator.
- Admin tokens carry their brand and are checked against `X-Lobster-Brand`. Admin API `brand` query parameters are data filters, not request context: the `bihuo` super administrator may inspect all brands while agents remain scoped to their own brand.

## Adding A New Endpoint

- Public endpoint: accept `Request` and call `resolve_request_brand_mark(request, body.brand_mark)`.
- Authenticated endpoint: depend on `get_current_user`; do not reimplement JWT parsing.
- Online-to-cloud call: build headers with `with_oem_brand_header()`.
- New browser or Mini Program call: use the existing shared request wrapper.
- Never default an explicit body model field to `bihuo`; use `None` and let the resolver apply the legacy default after checking all supplied values.
