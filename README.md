# Westmanga API by Nat

Wrapper **v1.westmanga.my** — By: Nat · **v1.0.1**

## Endpoint
| Path | Fungsi |
|------|--------|
| `GET /api/latest?page=1` | Update (`orderBy=Update`) |
| `GET /api/projects?page=1` | Project Westmanga |
| `GET /api/search?q=` | Cari |
| `GET /api/detail/{slug}` | Detail + chapter |
| `GET /api/chapter?chapter=` | Gambar chapter |

## Auth
1. `GET /api/visit-token` → header `X-WM-Visit`
2. Fallback `data.mantweh.online` + HMAC

## Deploy
Upload ke GitHub → Vercel (root = folder ini).

Env opsional: `WESTMANGA_BASE`, `WESTMANGA_TIMEOUT`, `WESTMANGA_PER_PAGE`

## Cloudflare
IP datacenter/Vercel sering **403**. Bukan salah mapping endpoint.
