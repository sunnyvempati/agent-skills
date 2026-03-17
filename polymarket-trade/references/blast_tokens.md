# BLAST Rotterdam Token IDs

## Group A (March 18)

### FURIA vs TYLOO
- Slug: `cs2-furia-tyloo-2026-03-18`
- TYLOO YES: `0x5e63cdbffe37bdd9410dcc1b4ae5a234c74821478bce9b419a2a0c292c052bad`
- FURIA YES: `0x573c4a5867bf23ca34d2d57a85687cb1b35c3d7595b73f6039e7c1c1c883a129`
- Condition: `0x4c4e8d4341d6f15d541073de1ba5db9a5c079915a3a4627559a6ccc5c4ef0490`

### FaZe vs Aurora
- Slug: `cs2-faze-aur1-2026-03-18`

### Falcons vs NRG
- Slug: `cs2-fal2-nrg-2026-03-18`

## Group B (March 19)

### PARIVISION vs NIP
- Slug: `cs2-prv-nip-2026-03-19`
- NIP YES: `0x798b2cf6733a73bb6d1fa23a5e2c8a4116537e57d14a357017e4414b76efba1a`
- NIP YES numeric: `54975756741700526651182388742103546205551522433650567751050274150295578589722`
- PARIVISION YES: `0xf9d881a7d4d765838475d1d1ae8942fd110013e37e04beaf6f302216176fbdfa`
- Condition: `0xe4c1e33149a57001f047dbae47bbde6027f7d3fdcfefb142fcdc95c60abf4a29`

### Spirit vs Liquid
- Slug: `cs2-spirit-liquid-2026-03-19`

### MOUZ vs MongolZ
- Slug: (check polymarket)

### Vitality vs 9z
- Slug: (check polymarket)

## Outrights
- Falcons outright: `will-team-falcons-win-blast-open-rotterdam-2026`
- NaVi outright: `will-natus-vincere-win-blast-open-rotterdam-2026`
- Vitality outright: `will-vitality-win-blast-open-rotterdam-2026`

## Finding Token IDs
```bash
polymarket -o json markets slug "cs2-furia-tyloo-2026-03-18"
```
The numeric token ID is in the `tokens[].token_id` field.
