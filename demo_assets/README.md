# Demo asset packet

One of each file the `/claims/upload` form asks for, so you can try it
immediately without hunting for real documents. This is packet A from the
seeded demo (SPEC.md section 7.3) — a clean claim where every rule passes.

| File | Upload into |
|---|---|
| `ad.png` | Ad (image or PDF) |
| `invoice.png` | Invoice |
| `payment.png` | Proof of payment |
| `claim_form.png` | Claim form |
| `affidavit.png` | Station affidavit (only needed if medium = radio) |

## Try it

1. Go to `/claims/upload`.
2. Mode: **Full claim**. Medium: **Direct mail**. Submitted on: any 2026 date
   within 60 days of the invoice (invoice date is 2026-09-14, so e.g. `2026-10-01`).
   Ad width/height: `6` x `4`.
   Skip the affidavit — that's only for radio claims.
3. Upload the four files above (`ad.png`, `invoice.png`, `payment.png`,
   `claim_form.png`) and submit.

Expected result: **filed automatically**, payable **$380** (cost $760 at a
50% rate), every rule passing — checked live against whichever guide is
currently active (upload your own at `/program/upload` first if you want to
see it checked against real guidelines instead of the seeded Northwind one).

To see a rule fail instead, try `ad.png` alone in **pre-check** mode after
first uploading a guide with a logo-size rule the ad doesn't meet.
