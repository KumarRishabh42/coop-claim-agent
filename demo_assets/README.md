# Demo asset packet

Ready-to-upload files for `/claims/upload` and `/program/upload`, so you can
try the app immediately without hunting for real documents.

| File | Upload into | What it shows |
|---|---|---|
| `ad-pass.png` | Ad | Every rule passes — clean logo sizes, tagline present |
| `ad-fail.png` | Ad | Brand logo too small (0.7 in, needs ~1.2 in) — fails on section 3 |
| `bmw-ad-pass.png` / `bmw-ad-fail.png` | Ad | Same idea, BMW Motorrad-themed. Includes a real motorcycle photo — [BMW motorcycle vintage.jpg](https://commons.wikimedia.org/wiki/File:BMW_motorcycle_vintage.jpg) by Adonis Chen, [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/), via Wikimedia Commons |
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

### A real-world example: BMW Motorrad's 2026 co-op guidelines

[2026 Co-op Guidelines (PDF)](https://www.bmwmotorraddealerprograms.com/docs/2026-Co-op%20Guidelines.pdf)
— BMW's own public dealer-program site. Download it and upload it at
`/program/upload` to see rule extraction run against a real, 16-page
manufacturer guide instead of the synthetic one above (a bigger, messier
document than this prototype's 9 supported rule types fully cover — some
rules get skipped rather than mis-decided, which is expected). Then pair it
with `bmw-ad-pass.png` / `bmw-ad-fail.png` in this folder.

We're linking to BMW's own copy rather than mirroring the PDF in this repo —
it's their document.
