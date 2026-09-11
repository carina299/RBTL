# RBTLove (Work in Progress)

> A private 1:1 chat channel + long-term memory for an AI companion + an album/memories feature
> Derived from [Tidal_Echo](https://github.com/anhe2021212-spec/Tidal_Echo) (AGPLv3)

---

## What this is

A private 1:1 AI companion chat system: a phone PWA talks to an AI running locally
(Claude Code + a channel plugin). Building on top of the original project, we're
planning several additions:

1. **Long-term memory system** — lets the AI recall things from past conversations via vector retrieval + automatic extraction
2. **Album feature, end to end** — photo upload, captions, timeline browsing (the original project left this as an empty UI shell)

## Contributors

- [carina299] — backend
- [czhang11zhangyingmeng] — frontend

## Running locally (backend, partially working)

```bash
cd backend
cp .env.example relay.env   # fill in RELAY_SECRET and other required values
docker-compose up --build
```

> Only Part 1 (the database layer) is done so far; the rest of the API isn't
> implemented yet. This is for development/debugging only at this stage.

## License

This project is a derivative work of [Tidal_Echo](https://github.com/anhe2021212-spec/Tidal_Echo),
licensed under its current **GNU AGPLv3** license (see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE)).