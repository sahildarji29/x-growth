# XActions — Claude Code Instructions

> X/Twitter automation toolkit: browser scripts, CLI, Node.js library, MCP server, web dashboard. No API fees. By nichxbt.

## Quick Reference

| User Request | Solution |
|---|---|
| Unfollow everyone | `src/unfollowEveryone.js` |
| Unfollow non-followers | `src/unfollowback.js` |
| Download Twitter video | `scripts/videoDownloader.js` |
| Detect unfollowers | `src/detectUnfollowers.js` |
| Train algorithm for a niche | `src/automation/algorithmBuilder.js` (browser) or `xactions persona create` (CLI) |
| Become a thought leader / grow account | `skills/algorithm-cultivation/SKILL.md` |
| 24/7 LLM-powered growth agent | `src/algorithmBuilder.js` + `src/personaEngine.js` — run via `xactions persona run <id>` |
| Create a persona for automation | `xactions persona create` or MCP tool `x_persona_create` |
| Twitter automation without API | XActions uses browser automation |
| MCP server for Twitter | `src/mcp/server.js` |

## Architecture Overview

XActions has **three runtime contexts** — understanding which context code runs in is critical:

| Context | Where it runs | Entry point | Key constraint |
|---|---|---|---|
| **Browser scripts** | DevTools console on x.com | IIFE, paste in console | No Node.js APIs, uses DOM & `sessionStorage` |
| **Node.js library/CLI/MCP** | Local machine or server | `src/cli/index.js`, `src/mcp/server.js` | Uses Puppeteer for browser automation |
| **API server** | Express.js backend | `api/server.js` | PostgreSQL via Prisma, Redis for job queue |

### Tech Stack

- **Runtime**: Node.js >= 18, ESM (`"type": "module"` in package.json)
- **Backend**: Express.js with Helmet, CORS, rate limiting, Morgan logging
- **Database**: PostgreSQL via Prisma ORM (`prisma/schema.prisma`)
- **Job Queue**: Bull + Redis
- **Browser Automation**: Puppeteer + puppeteer-extra-plugin-stealth
- **Testing**: Vitest 4.x (run: `vitest run`, watch: `vitest`)
- **MCP**: `@modelcontextprotocol/sdk` — server at `src/mcp/server.js`
- **Payments**: Stripe + x402 crypto payments
- **Realtime**: Socket.io

## Project Structure

```
src/                → Core library
  ├── cli/          → CLI commands (commander.js)
  ├── mcp/          → MCP server for AI agents (Claude Desktop, etc.)
  ├── scrapers/     → Puppeteer-based scrapers (twitter/, bluesky/, mastodon/, threads/)
  ├── client/       → HTTP-only Twitter client (no Puppeteer needed)
  ├── automation/   → Browser automation scripts (require core.js pasted first)
  ├── agents/       → Thought leader agent, persona engine
  ├── analytics/    → Engagement analytics, audience overlap
  ├── streaming/    → Real-time tweet streaming
  ├── plugins/      → Plugin system
  ├── ai/           → LLM integrations
  ├── a2a/          → Agent-to-Agent protocol
  ├── workflows/    → Multi-step automation workflows
  ├── utils/        → Shared utilities
  └── *.js          → Individual feature scripts (60+ browser-paste scripts)
api/                → Express.js backend
  ├── server.js     → Main server entry
  ├── routes/       → REST endpoints (auth, twitter, operations, ai/, etc.)
  ├── services/     → Business logic (jobQueue, scrapers, payments)
  ├── middleware/    → Auth, rate limiting, error handling
  └── realtime/     → Socket.io handlers
dashboard/          → Static HTML frontend (served by Express)
skills/             → 32 Agent Skills (skills/*/SKILL.md)
tests/              → Vitest tests (agents/, client/, http-scraper/, a2a/)
types/              → TypeScript declarations (index.d.ts)
prisma/             → Database schema + migrations
scripts/            → Standalone utility scripts
config/             → Agent config, niche configs, persona templates
docs/               → Documentation
  └── agents/       → Agent-specific references (selectors.md, patterns, contributing, agent-troubleshooting.md)
extension/          → Browser extension (Chrome/Edge)
bin/                → CLI entry point (unfollowx)
archive/            → Legacy browser-only scripts (do not modify)
```

## Skills

49 skills in `skills/*/SKILL.md`. **Read the relevant SKILL.md before implementing** when a user's request matches a category. Use `skills/index.json` for programmatic discovery. New skills: copy `skills/TEMPLATE.md`.

- **Unfollow management** — mass unfollow, non-follower cleanup
- **Analytics & insights** — engagement, hashtags, competitors, best times
- **Content posting** — tweets, threads, polls, scheduling, reposts
- **Twitter scraping** — profiles, followers, tweets, media, bookmarks
- **Growth automation** — auto-like, follow engagers, keyword follow
- **Algorithm cultivation** — thought leader training, niche optimization
- **Community management** — join/leave communities
- **Follower monitoring** — follower alerts, continuous tracking
- **Blocking & muting** — bot blocking, bulk mute
- **Content cleanup** — delete tweets, unlike, clear history
- **Direct messages** — auto DM, message management
- **Bookmarks** — export, organize, folder management
- **Lists** — create, manage, bulk add members
- **Profile management** — edit profile, avatar, header, bio
- **Settings & privacy** — protected tweets, notification preferences
- **Notifications management** — filtering, auto-response, notification controls
- **Engagement & interaction** — auto-reply, auto-repost, plug replies
- **Discovery & explore** — trending, topics, search
- **Premium & subscriptions** — subscription features
- **Spaces & live** — create, join, schedule spaces
- **Grok AI** — chat, image generation
- **Articles & longform** — compose, publish articles
- **Business & ads** — campaigns, boosts, ads dashboard
- **Creator monetization** — revenue, analytics
- **Community health monitoring** — follower quality audits, engagement authenticity
- **Competitor intelligence** — competitor profile, content, and audience analysis
- **Content repurposing** — repackage top tweets into threads, carousels, variations
- **Lead generation** — find and qualify B2B leads from X conversations
- **Viral thread generation** — research trends and generate high-engagement threads
- **A2A multi-agent** — Agent-to-Agent protocol integration
- **XActions CLI** — `bin/unfollowx` command-line tool
- **Account backup** — export tweets, likes, bookmarks, followers as JSON; trigger official data archive
- **Video downloading** — download videos and GIFs from tweets, batch download, quality selection
- **X Pro management** — navigate TweetDeck, setup monitoring columns, multi-column view
- **Saved searches** — create, manage, run, and export saved search queries
- **Delegate access** — add/remove account delegates and configure posting permissions
- **Community notes** — view, write, rate, and browse Community Notes on posts
- **Post editing** — edit existing posts or undo a tweet within the 30-second window (Premium)
- **Account tools** — join date, login history, connected accounts, appeal suspension, QR codes, share/embed
- **Media studio** — manage media library, upload, video analytics, monetization, captions
- **Timeline viewing** — switch For You/Following, auto-scroll, collect and export timeline posts
- **Topic management** — follow/unfollow X Topics, discover topics by keyword
- **CRM management** — tag, segment, search, and sync followers as CRM contacts
- **Teams management** — create teams, invite members, assign roles for collaborative automation
- **Webhooks** — receive real-time event notifications for operations, follower changes
- **Graph analysis** — network graph algorithms: PageRank, betweenness, community detection
- **Billing management** — manage Stripe subscriptions, checkout, billing portal, plan changes
- **x402 payments** — crypto pay-per-use API access via the x402 protocol, multi-chain/token
- **XActions MCP server** — `src/mcp/server.js` for AI agents

## Key Technical Context

- Browser scripts run in **DevTools console on x.com**, not Node.js
- DOM selectors change frequently — see [selectors.md](docs/agents/selectors.md)
- Scripts in `src/automation/` require pasting `src/automation/core.js` first
- State persistence uses `sessionStorage` (lost on tab close)
- CLI entry point: `bin/unfollowx`, installed via `npm install -g xactions`
- MCP server: `src/mcp/server.js` — used by Claude Desktop and AI agents
- Prefer `data-testid` selectors — most stable across X/Twitter UI updates
- X enforces aggressive rate limits; all automation must include 1-3s delays between actions

## Commands

```bash
# Development
npm run dev              # Start API server with nodemon (NODE_ENV=development)
npm run start            # Start API server for production
npm run cli              # Run CLI
npm run mcp              # Start MCP server
npm run agent            # Run thought leader agent
npm run worker           # Start Bull job queue worker

# Testing
vitest run               # Run all tests once
vitest run tests/agents  # Run specific test directory
vitest                   # Watch mode

# Database
npx prisma migrate dev   # Run migrations
npx prisma db push       # Push schema changes
npx prisma studio        # Open Prisma Studio GUI
```

## Environment Variables

Copy `.env.example` for the full list. Key variables:
- `DATABASE_URL` — PostgreSQL connection string (required in production)
- `JWT_SECRET` — Auth token signing (required in production)
- `REDIS_HOST`, `REDIS_PORT` — For Bull job queue
- `XACTIONS_SESSION_COOKIE` — Twitter session cookie for MCP/CLI
- `PUPPETEER_HEADLESS` — `true`/`false` for browser automation

## Database Schema

PostgreSQL with Prisma ORM. Key models in `prisma/schema.prisma`:
- `User` — auth (Twitter OAuth + session cookies), credits, admin flags
- `Subscription` — Stripe billing tiers (free/basic/pro/enterprise)
- `Operation` — tracks automation jobs (type, status, progress, result)
- `JobQueue` — Bull job persistence
- `AccountSnapshot` / `FollowerSnapshot` / `FollowerChange` — follower monitoring over time

## Patterns & Style

- Browser script patterns: [browser-script-patterns.md](docs/agents/browser-script-patterns.md)
- Adding features: [contributing-features.md](docs/agents/contributing-features.md)
- DOM selectors (verified January 2026): [selectors.md](docs/agents/selectors.md)
- `const` over `let`, async/await, emojis in `console.log`
- Author credit: `// by nichxbt`
- ESM imports only (`import`/`export`, no `require`)
- Error messages use emoji prefixes: ❌ error, ⚠️ warning, ✅ success, 🔄 progress

## Testing Conventions

- Test framework: Vitest 4.x, config in `vitest.config.js`
- Tests live in `tests/` mirroring source structure
- **No mocks, stubs, or fakes** — real implementations only
- Test files: `*.test.js`
- Environment: Node.js (not jsdom, despite it being a dev dep)
- Timeouts: 30s per test, 30s for hooks
- `tests/x402-integration.test.js` requires a running server — failures are expected when server is down
- See `docs/agents/agent-troubleshooting.md` for common test failure patterns and fixes

## Codespace Performance

```bash
ps aux --sort=-%cpu | head -20    # See top CPU consumers
pkill -f "vitest"                  # Kill vitest workers
pkill -f "tsgo --noEmit"          # Kill type-checker
```

Common resource hogs: `tsgo --noEmit` (~500% CPU), vitest workers (15x ~100% CPU each), multiple tsserver instances.

## Terminal Management

- Always use background terminals (`isBackground: true`) for every command
- Always kill the terminal after the command completes
- Do not reuse foreground shell sessions — stale sessions block future operations
- If a terminal appears unresponsive, kill it and create a new one

## Mandatory Rules

1. **Never mock, stub, or fake anything.** Real implementations only.
2. **TypeScript strict mode** — no `any`, no `@ts-ignore`.
3. **Always kill terminals** after commands complete.
4. **Always commit and push** as `nirholas`.
