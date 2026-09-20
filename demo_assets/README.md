# Demo asset packet

Ready-to-upload files for `/claims/upload` and `/program/upload`, so you can
try the app immediately without hunting for real documents.

| File | Upload into | What it shows |
|---|---|---|
| `ad-pass.png` | Ad | Every rule passes — clean logo sizes, tagline present |
| `ad-fail.png` | Ad | Brand logo too small (0.7 in, needs ~1.2 in) — fails on section 3 |
| `invoice.png` | Invoice | $760, dated 2026-09-14 |
| `payment.png` | Proof of payment | $760, matches the invoice |
| `claim_form.png` | Claim form | $760, matches |
| `affidavit.png` | Station affidavit | Only needed if medium = radio |
| `northwind-2026-guide.md` | `/program/upload` | The synthetic co-op guide these packets are checked against |

## Try it — the fast way

1. Go to `/claims/upload`.
2. Upload **only** `ad-pass.png` as the Ad. Leave everything else blank —
   invoice/payment/claim form default to a matching $760 sample automatically.
3. Submit. Expect: **filed automatically**, $380 payable, every rule passing.

Swap in `ad-fail.png` instead (same steps, mode = **Pre-check**) to see a
rule fail with a specific, cited fix.

## Try it against a real guide

Upload `northwind-2026-guide.md` at `/program/upload` first (or your own real
PDF), then repeat the steps above — the packet is checked live against
whichever guide is currently active.
