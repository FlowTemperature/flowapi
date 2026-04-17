<div align="center">

# ⚡ Flow API

**API de IA gratuita e open source — powered by Groq & OpenAI**

[![Status](https://img.shields.io/badge/status-production-a78bfa?style=flat-square)](https://flow.squareweb.app/status)
[![License](https://img.shields.io/badge/license-MIT-10b981?style=flat-square)](LICENSE)
[![Made in Brazil](https://img.shields.io/badge/feito_com_%E2%99%A5-no_Brasil-009c3b?style=flat-square)]()

[🌐 flow.squareweb.app](https://flow.squareweb.app) · [📊 Status](https://flow.squareweb.app/status) · [🔒 Privacidade](https://flow.squareweb.app/privacy)



---

## O que é a Flow API?

Flow API é um projeto open source que oferece acesso **gratuito** a múltiplos modelos de IA — Groq e OpenAI — através de uma API REST simples e rápida. O objetivo é democratizar o acesso à inteligência artificial para desenvolvedores, estudantes e curiosos, sem burocracia, sem cartão de crédito e sem complicação.

Qualquer pessoa pode criar uma conta, gerar uma chave API e começar a integrar IA no seu projeto em menos de 1 minuto.

---

## Por que existe?

Acesso a modelos de linguagem modernos costuma exigir cartão de crédito, aprovação de conta ou limites absurdamente baixos no plano gratuito. A Flow API nasceu para resolver isso: uma camada simples em cima das APIs existentes, com autenticação própria, controle de uso e interface amigável — tudo open source para quem quiser aprender, contribuir ou hospedar a própria instância.

---

## Funcionalidades

- 🆓 **Gratuita** — 2.000 requests por dia sem custo
- ⚡ **Ultra-rápida** — infraestrutura Groq com latência mínima
- 🤖 **Múltiplos modelos** — Llama, GPT e modelos especializados
- 🧠 **Seleção automática** — o modo `auto` escolhe o melhor modelo pro seu prompt
- 🔑 **Chaves próprias** — gerencie múltiplas chaves pelo dashboard
- 📊 **Monitoramento de uso** — veja seu consumo diário em tempo real
- 🔄 **Compatível com OpenAI SDK** — troca a base URL e já funciona
- 🌐 **Open source** — código aberto, sem segredos

---

## Como usar

Crie sua conta em [flow.squareweb.app](https://flow.squareweb.app), gere uma chave API no dashboard e faça sua primeira chamada:

```bash
curl -X POST "https://flow.squareweb.app/generate" \
  -H "Authorization: Bearer flow_SUA_CHAVE" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explique o que é uma API", "mode": "auto"}'
```

Resposta:

```json
{
  "response": "Uma API (Application Programming Interface) é...",
  "model": "llama-3.3-70b-versatile",
  "tokens_used": 187
}
```

---

## Modelos disponíveis

| Modelo | Melhor para |
|--------|------------|
| `llama-3.1-8b-instant` | Conversas rápidas e simples |
| `llama-3.3-70b-versatile` | Raciocínio, análise e criatividade |
| `openai/gpt-oss-120b` | Código e programação |
| `groq/compound` | Respostas estruturadas |
| `groq/compound-mini` | Resumos rápidos |
| `gpt-4o-mini` | Tarefas gerais econômicas |

Ou use `"mode": "auto"` e o sistema escolhe o modelo ideal automaticamente.

---

## Stack

- **Backend** — Python + FastAPI
- **Modelos** — Groq API + OpenAI API
- **Banco de dados & Auth** — Supabase (PostgreSQL + Auth)
- **AVISO!⚠️**: — Não armazenamos mensagens nem dados de usuários.
- **Deploy** — Squareweb

---

## Contribuindo

Contribuições são muito bem-vindas! Seja corrigindo um bug, melhorando a documentação ou sugerindo uma nova feature.

1. Fork o repositório
2. Crie uma branch: `git checkout -b minha-feature`
3. Commit suas mudanças: `git commit -m 'feat: minha feature'`
4. Push: `git push origin minha-feature`
5. Abra um Pull Request

Para dúvidas ou sugestões, abra uma [issue](../../issues) ou fale pelo chat no site.

---

## Licença

MIT — use, modifique e distribua à vontade. Feito com ♥ no Brasil.
