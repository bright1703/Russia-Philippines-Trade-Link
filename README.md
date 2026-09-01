# Russia–Philippines Trade Link

Independent B2B trade platform and contact point connecting Philippine importers and distributors with selected supplier opportunities from Primorsky Krai and the Russian Far East.

Live site: [rusphiltrade.com](https://rusphiltrade.com/)

The page presents supplier opportunities across food, agriculture and fertilizers, packaging, vehicles and industrial equipment. Product availability, import eligibility, documentation, pricing and delivery terms are confirmed separately for each inquiry.

## Run locally

Open `index.html` directly in a browser, or serve the directory with any static web server:

```bash
python -m http.server 8000
```

The buyer inquiry and counterparty check forms submit to Formspree with an inline success or error state. Direct email, WhatsApp and Viber links remain available as alternative contact channels.

## AI monitoring pipeline

The separate Python pipeline is in [`trade-agent/`](trade-agent/README.md).
It collects and analyzes trade opportunities independently from the static
GitHub Pages site. GitHub Pages hosts the site only. The pipeline must run on
a Python server or local machine according to [`trade-agent/DEPLOY.md`](trade-agent/DEPLOY.md).
