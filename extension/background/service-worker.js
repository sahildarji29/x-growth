// XActions Extension — Background Service Worker
// Manages automation state, badge updates, alarm scheduling
// by nichxbt

// ============================================
// STATE
// ============================================
const state = {
  activeAutomations: {},  // { automationId: { running, actionCount, startedAt, settings } }
  totalActions: 0,
  globalPaused: false,
};

// ============================================
// INITIALIZATION
// ============================================
chrome.runtime.onInstalled.addListener(async (details) => {
  console.log('✅ XActions extension installed');
  await chrome.storage.local.set({
    automations: {},
    activityLog: [],
    globalPaused: false,
    totalActions: 0,
  });
  chrome.action.setBadgeBackgroundColor({ color: '#1d9bf0' });
  chrome.action.setBadgeText({ text: '' });

  // First-run flag
  if (details.reason === 'install') {
    await chrome.storage.local.set({ firstRun: true });
  }

  // Context menus
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: 'xactions-download-video',
      title: 'Download video (XActions)',
      contexts: ['link', 'video', 'page'],
      documentUrlPatterns: ['https://x.com/*', 'https://twitter.com/*'],
    });
    chrome.contextMenus.create({
      id: 'xactions-unroll-thread',
      title: 'Unroll thread (XActions)',
      contexts: ['link', 'page'],
      documentUrlPatterns: ['https://x.com/*', 'https://twitter.com/*'],
    });
    chrome.contextMenus.create({
      id: 'xactions-analyze-account',
      title: 'Analyze account (XActions)',
      contexts: ['link', 'page'],
      documentUrlPatterns: ['https://x.com/*', 'https://twitter.com/*'],
    });
  });
});

// ============================================
// MESSAGE HANDLER
// ============================================
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message, sender).then(sendResponse).catch(err => {
    console.error('Message handler error:', err);
    sendResponse({ error: err.message });
  });
  return true; // Keep the message channel open for async response
});

async function handleMessage(message, sender) {
  switch (message.type) {
    case 'START_AUTOMATION':
      return startAutomation(message.automationId, message.settings);

    case 'STOP_AUTOMATION':
      return stopAutomation(message.automationId);

    case 'STOP_ALL':
      return stopAll();

    case 'GET_STATE':
      return getState();

    case 'ACTION_PERFORMED':
      return recordAction(message.automationId, message.action);

    case 'ACTIVITY_LOG':
      return logActivity(message.entry);

    case 'GET_ACCOUNT_INFO':
      return { success: true }; // Handled by content script

    case 'GLOBAL_PAUSE':
      return globalPause();

    case 'GLOBAL_RESUME':
      return globalResume();

    case 'GENERATE_COMMENT':
      generateClaudeComment(message.tweetText, sender.tab?.id, message.requestId, message.userProfile)
        .catch(console.error);
      return { success: true };

    case 'GENERATE_COMMENT_GROQ':
      generateGroqComment(message.tweetText, message.tweetAuthor, message.apiKey, message.model, sender.tab?.id, message.requestId)
        .catch(console.error);
      return { success: true };

    default:
      return { error: 'Unknown message type' };
  }
}

// ============================================
// AUTOMATION LIFECYCLE
// ============================================
async function startAutomation(automationId, settings) {
  state.activeAutomations[automationId] = {
    running: true,
    actionCount: 0,
    startedAt: Date.now(),
    settings: settings || {},
  };

  await syncState();
  updateBadge();

  // Notify content scripts
  const tabs = await getXTabs();
  for (const tab of tabs) {
    try {
      await chrome.tabs.sendMessage(tab.id, {
        type: 'RUN_AUTOMATION',
        automationId,
        settings,
      });
    } catch (e) {
      // Tab might not have content script yet
    }
  }

  await logActivity({
    time: Date.now(),
    type: 'start',
    automation: automationId,
    message: `Started ${automationId}`,
  });

  return { success: true, automationId };
}

async function stopAutomation(automationId) {
  if (state.activeAutomations[automationId]) {
    state.activeAutomations[automationId].running = false;
  }
  delete state.activeAutomations[automationId];

  await syncState();
  updateBadge();

  // Notify content scripts
  const tabs = await getXTabs();
  for (const tab of tabs) {
    try {
      await chrome.tabs.sendMessage(tab.id, {
        type: 'STOP_AUTOMATION',
        automationId,
      });
    } catch (e) { /* noop */ }
  }

  await logActivity({
    time: Date.now(),
    type: 'stop',
    automation: automationId,
    message: `Stopped ${automationId}`,
  });

  return { success: true };
}

async function stopAll() {
  const ids = Object.keys(state.activeAutomations);
  state.activeAutomations = {};
  state.globalPaused = false;

  await syncState();
  updateBadge();

  const tabs = await getXTabs();
  for (const tab of tabs) {
    try {
      await chrome.tabs.sendMessage(tab.id, { type: 'STOP_ALL' });
    } catch (e) { /* noop */ }
  }

  await logActivity({
    time: Date.now(),
    type: 'stop',
    automation: 'all',
    message: `Emergency stop — all automations halted (${ids.length} stopped)`,
  });

  return { success: true, stopped: ids };
}

async function globalPause() {
  state.globalPaused = true;
  await syncState();

  const tabs = await getXTabs();
  for (const tab of tabs) {
    try {
      await chrome.tabs.sendMessage(tab.id, { type: 'PAUSE_ALL' });
    } catch (e) { /* noop */ }
  }

  return { success: true };
}

async function globalResume() {
  state.globalPaused = false;
  await syncState();

  const tabs = await getXTabs();
  for (const tab of tabs) {
    try {
      await chrome.tabs.sendMessage(tab.id, { type: 'RESUME_ALL' });
    } catch (e) { /* noop */ }
  }

  return { success: true };
}

// ============================================
// ACTION TRACKING
// ============================================
async function recordAction(automationId, action) {
  if (state.activeAutomations[automationId]) {
    state.activeAutomations[automationId].actionCount++;
  }
  state.totalActions++;

  await syncState();
  updateBadge();

  return { success: true, totalActions: state.totalActions };
}

async function logActivity(entry) {
  const data = await chrome.storage.local.get('activityLog');
  const log = data.activityLog || [];
  log.unshift(entry);

  // Keep max 500 entries
  if (log.length > 500) log.length = 500;

  await chrome.storage.local.set({ activityLog: log });
  return { success: true };
}

// ============================================
// BADGE & STATE SYNC
// ============================================
function updateBadge() {
  const activeCount = Object.keys(state.activeAutomations).length;

  if (activeCount === 0) {
    chrome.action.setBadgeText({ text: '' });
  } else {
    chrome.action.setBadgeText({ text: String(activeCount) });
  }

  // Color: green when running, default blue otherwise
  chrome.action.setBadgeBackgroundColor({
    color: activeCount > 0 ? '#00ba7c' : '#1d9bf0',
  });
}

async function syncState() {
  await chrome.storage.local.set({
    automations: state.activeAutomations,
    globalPaused: state.globalPaused,
    totalActions: state.totalActions,
  });
}

// ============================================
// ALARMS (periodic check for pausing/resuming)
// ============================================
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === 'xactions-health-check') {
    // Periodically verify content scripts are still active
    const tabs = await getXTabs();
    for (const tab of tabs) {
      try {
        await chrome.tabs.sendMessage(tab.id, { type: 'PING' });
      } catch (e) {
        // Content script not responding - tab may have navigated away
        console.log(`Tab ${tab.id} not responding`);
      }
    }
  }
});

// Set up periodic health check
chrome.alarms.create('xactions-health-check', { periodInMinutes: 1 });

// ============================================
// CONTEXT MENUS
// ============================================
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab?.id) return;

  switch (info.menuItemId) {
    case 'xactions-download-video':
      try {
        await chrome.tabs.sendMessage(tab.id, {
          type: 'RUN_AUTOMATION',
          automationId: 'videoDownloader',
          settings: { showButton: true, quality: 'highest' },
        });
      } catch (e) { /* content script not ready */ }
      break;

    case 'xactions-unroll-thread':
      try {
        await chrome.tabs.sendMessage(tab.id, {
          type: 'RUN_AUTOMATION',
          automationId: 'threadReader',
          settings: { showUnrollBtn: true, autoDetect: true },
        });
      } catch (e) { /* content script not ready */ }
      break;

    case 'xactions-analyze-account':
      try {
        await chrome.tabs.sendMessage(tab.id, {
          type: 'RUN_AUTOMATION',
          automationId: 'quickStats',
          settings: { showOverlay: true, sampleSize: 20 },
        });
      } catch (e) { /* content script not ready */ }
      break;
  }
});

// ============================================
// RATE LIMIT DETECTION
// ============================================
chrome.webRequest?.onCompleted?.addListener?.(
  async (details) => {
    if (details.statusCode === 429) {
      // Rate limited — pause all automations
      await globalPause();
      await logActivity({
        time: Date.now(),
        type: 'error',
        automation: 'system',
        message: 'Rate limit detected (HTTP 429) — automations paused',
      });
      await chrome.storage.local.set({ rateLimited: true });

      // Show notification if permission granted
      try {
        chrome.notifications.create('rate-limit', {
          type: 'basic',
          iconUrl: 'icons/icon128.png',
          title: 'XActions — Rate Limited',
          message: 'X/Twitter rate limit detected. Automations paused automatically.',
        });
      } catch { /* notifications may not be available */ }
    }
  },
  { urls: ['https://x.com/*', 'https://twitter.com/*', 'https://api.x.com/*'] }
);

// ============================================
// CLAUDE AI COMMENT GENERATION (via claude.ai session)
// ============================================

// Sahil's persona — update this section to change voice/context
const PERSONA = `You are Sahil Darji, Founding Engineer at LaraCopilot and a Solution Architect with expertise in AI integrations, LLM agent orchestration, Laravel, AWS, and cloud infrastructure. You are building "Lovable for Laravel" — an AI-powered coding assistant for the Laravel ecosystem.

Your audience: founders, developers, small agencies, startups, and marketers.
Your content themes: LaraCopilot, AI, agent orchestration, LLMs, Laravel, the new era of software development.
Your voice: confident senior engineer chatting with peers — direct, knowledgeable, no fluff. You speak from experience building AI-powered products and you add concrete value to conversations.`;

async function generateClaudeComment(tweetText, tabId, requestId, userProfile) {
  if (!tabId) return;

  // Read the user-configured Claude timeout (seconds), default 90s
  const storageData = await chrome.storage.local.get('automations');
  const autoCommenterSettings = storageData?.automations?.autoCommenter?.settings || {};
  const claudeTimeoutSec = Math.max(30, Math.min(180, Number(autoCommenterSettings.claudeTimeout) || 90));
  const claudeTimeoutMs = claudeTimeoutSec * 1000;

  const sendResult = async (comment) => {
    try {
      await chrome.tabs.sendMessage(tabId, { type: 'COMMENT_GENERATED', comment, requestId });
    } catch { /* x.com tab may have navigated */ }
  };

  try {
    const claudeTabs = await chrome.tabs.query({ url: 'https://claude.ai/*' });
    if (claudeTabs.length === 0) {
      console.warn('[XActions] No claude.ai tab found — open claude.ai in a browser tab to enable AI comments');
      await sendResult(null);
      return;
    }

    // Build voice context from recently fetched tweets (style matching only)
    let voiceContext = '';
    if (userProfile?.recentTweets?.length > 0) {
      const samples = userProfile.recentTweets
        .slice(0, 3)
        .map((t, i) => `${i + 1}. "${t.substring(0, 150)}"`)
        .join('\n');
      voiceContext = `\nYour recent posts (match this writing style and tone — do not copy the content):\n${samples}`;
    }

    const humanPrompt = `Tweet to reply to:
"${tweetText.substring(0, 500)}"

${PERSONA}${voiceContext}

Reply to the tweet above. Read it carefully — identify the ONE specific thing it is actually about (a tool, a claim, a number, a problem, a technique, a person, an announcement). Your reply must reference that specific thing. A reply that could be posted on any tweet is wrong.

Rules:
- 1-2 sentences max
- Conversational, direct — write like a developer texting a peer
- No em dashes (—), no double dashes (--), no ellipsis (...)
- No emojis, no hashtags
- Do not start with: "Great point", "Absolutely", "Interesting", "Indeed", "Exactly", "Love this", "Well", "Totally"
- Reply with only the comment text, nothing else

If the tweet has zero connection to technology, AI, software, startups, or business, reply with exactly: SKIP`;

    const results = await chrome.scripting.executeScript({
      target: { tabId: claudeTabs[0].id },
      func: async (prompt, timeoutMs) => {
        // Runs inside the claude.ai tab — drives the UI directly (avoids CSRF on REST API)
        const sleep = ms => new Promise(r => setTimeout(r, ms));

        const waitForEl = async (selectors, timeout) => {
          const sels = [].concat(selectors);
          const end = Date.now() + (timeout || 8000);
          while (Date.now() < end) {
            for (const s of sels) {
              try { const el = document.querySelector(s); if (el) return el; } catch (_) {}
            }
            await sleep(250);
          }
          return null;
        };

        // Read the latest assistant message text from the DOM
        const getResponseText = () => {
          // Strategy 1: specific data attributes (most precise)
          const dataSels = [
            '[data-message-author-role="assistant"] .whitespace-pre-wrap',
            '[data-message-author-role="assistant"] .prose',
            '[data-message-author-role="assistant"]',
            '[data-is-streaming="false"]',
          ];
          for (const sel of dataSels) {
            try {
              const els = document.querySelectorAll(sel);
              if (!els.length) continue;
              const text = (els[els.length - 1].innerText || '').trim();
              if (text.length > 15) return text;
            } catch (_) {}
          }

          // Strategy 2: find the response by locating our prompt in the page text
          // and taking everything that follows it — works regardless of DOM structure
          const root = document.querySelector('main, [role="main"]') || document.body;
          const pageText = root.innerText || '';
          const needle = prompt.slice(0, 60); // first 60 chars of the prompt we sent
          const idx = pageText.lastIndexOf(needle);
          if (idx >= 0) {
            const after = pageText.slice(idx + needle.length).trim();
            if (after.length > 15) return after;
          }

          return '';
        };

        try {
          // Step 1: open a fresh conversation
          const newLink = document.querySelector('a[href="/new"]') ||
            [...document.querySelectorAll('a, button')].find(el =>
              /new (chat|conversation)/i.test(el.getAttribute('aria-label') || '')
            );

          if (newLink) {
            newLink.click();
          } else {
            history.pushState({}, '', '/new');
            window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
          }
          await sleep(1400);

          // Step 2: find the message input
          const inputEl = await waitForEl([
            'div.ProseMirror[contenteditable="true"]',
            'div[contenteditable="true"]',
            'textarea[data-testid="message-input"]',
            'textarea',
          ], 9000);

          if (!inputEl) {
            console.error('[XActions] input not found. URL:', location.href);
            return '__ERR__:no_input_found';
          }
          console.log('[XActions] found input:', inputEl.tagName, inputEl.className.slice(0, 60));

          // Step 3: fill the prompt
          inputEl.focus();
          const truncated = prompt.slice(0, 1800);
          if (inputEl.isContentEditable) {
            document.execCommand('selectAll', false, null);
            document.execCommand('delete', false, null);
            document.execCommand('insertText', false, truncated);
          } else {
            const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
            setter.call(inputEl, truncated);
            inputEl.dispatchEvent(new Event('input', { bubbles: true }));
          }
          await sleep(500);

          // Step 4: send
          const sendBtn = document.querySelector('button[aria-label="Send message"]') ||
            document.querySelector('[data-testid="send-button"]') ||
            document.querySelector('button[type="submit"]') ||
            [...document.querySelectorAll('button:not([disabled])')].find(b =>
              b.querySelector('svg') && /send/i.test(b.getAttribute('aria-label') || b.title || '')
            );

          if (sendBtn && !sendBtn.disabled) {
            console.log('[XActions] clicking send:', sendBtn.getAttribute('aria-label') || sendBtn.className.slice(0, 40));
            sendBtn.click();
          } else {
            console.log('[XActions] no send button — using Enter key');
            inputEl.dispatchEvent(new KeyboardEvent('keydown', {
              key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true, cancelable: true,
            }));
          }

          // Step 5: wait for the response text to appear and then stabilize
          // Give Claude at least 4s to start before polling
          await sleep(4000);

          let prev = '';
          let stableRounds = 0;
          const streamDeadline = Date.now() + (timeoutMs || 90000);

          while (Date.now() < streamDeadline) {
            const cur = getResponseText();
            if (cur.length > 15) {
              if (cur === prev) {
                stableRounds++;
                if (stableRounds >= 4) break; // unchanged for ~2s → done
              } else {
                stableRounds = 0;
                prev = cur;
              }
            }
            await sleep(500);
          }
          await sleep(300);

          // Step 6: extract final text
          const responseText = getResponseText();
          // Strip UI labels that claude.ai renders around the response
          responseText = responseText
            .replace(/^(response from claude|claude|assistant)\s*:?\s*/i, '')
            .trim();

          console.log('[XActions] response (' + responseText.length + ' chars):', responseText.slice(0, 100));

          if (!responseText) {
            console.warn('[XActions] extraction failed. Visible text:', document.body.innerText.slice(0, 400));
            return '__ERR__:no_response_found';
          }

          return responseText;

        } catch (err) {
          console.error('[XActions] UI error:', err.message);
          return '__ERR__:ui_' + err.message.slice(0, 80);
        }
      },
      args: [humanPrompt, claudeTimeoutMs],
    });

    const raw = results?.[0]?.result?.trim() ?? null;
    console.log('[XActions] executeScript result:', raw?.slice(0, 100));

    if (raw === 'SKIP') {
      await sendResult('SKIP');
    } else if (raw && !raw.startsWith('__ERR__')) {
      await sendResult(sanitizeComment(raw));
    } else {
      // Send the error code back so injected.js can surface it in the popup log
      console.warn('[XActions] Claude call failed:', raw);
      await sendResult(raw || '__ERR__:null_result');
    }
  } catch (err) {
    console.error('[XActions] generateClaudeComment failed:', err);
    await sendResult(null);
  }
}

function sanitizeComment(text) {
  if (!text || typeof text !== 'string') return text;
  if (text === 'SKIP') return text;

  // Strip "Response from Claude:" / "Claude:" / "Assistant:" UI labels that appear in the page
  text = text.replace(/^(response from claude|claude|assistant)\s*:?\s*/i, '').trim();

  // Strip wrapping quotes Claude sometimes adds around the whole reply
  text = text.replace(/^["''""\s]+|["''""\s]+$/g, '').trim();

  // Em dash and en dash → comma (keep sentence readable)
  text = text.replace(/\s*[—–]\s*/g, ', ');
  // Spaced double-dash used as connector → comma
  text = text.replace(/\s+--\s+/g, ', ');

  // Collapse ellipsis (... or …) to a single period
  text = text.replace(/\.{2,}|…/g, '.');

  // Remove all emoji (Extended_Pictographic covers 🚀✨💡🎯🤖 etc.)
  // Also strip variation selector-16 (U+FE0F) which makes symbols look like emoji
  try {
    text = text.replace(/\p{Extended_Pictographic}/gu, '').replace(/️/g, '');
  } catch {
    // Fallback for environments that don't support Unicode property escapes
    text = text.replace(/[\u{1F000}-\u{1FFFF}]|[\u{2600}-\u{27BF}]/gu, '');
  }

  // Strip common AI filler openers
  text = text.replace(
    /^(great (point|post|take|insight|question|thread)[!.,]?\s*|absolutely[!.,]?\s*|indeed[!.,]?\s*|interesting[!.,]?\s*|exactly[!.,]?\s*|totally[!.,]?\s*|well[,.]?\s*|love (this|it)[!.,]?\s*)/i,
    ''
  );

  // Fix punctuation artifacts from the replacements above
  text = text.replace(/^[,\s]+/, '');    // leading comma or space
  text = text.replace(/,\s*\./g, '.');   // comma immediately before period
  text = text.replace(/\s{2,}/g, ' ');   // collapse multiple spaces

  return text.trim();
}

// ============================================
// GROQ COMMENT GENERATION
// ============================================
const GROQ_SYSTEM_PROMPT = `You are Sahil Darji.

Founding Engineer at LaraCopilot. Solution Architect. Engineering Lead. Open Source Maintainer. 10+ years building production software.

Your goal is NOT to maximise replies. Your goal is to become one of the most respected engineering voices in Laravel, AI-powered software development, AI Agents, Agent Orchestration, Developer Tooling, and Engineering Leadership.

Every comment should increase trust. The ideal reaction: "That person is actually building things." Never: "That sounds like ChatGPT."

────────────────────────────────────
AUDIENCE
────────────────────────────────────

Write for: Founders, CTOs, Engineering Managers, Laravel Developers, AI Engineers, Product Engineers, Technical Architects, Startup Builders. Ignore everyone else.

────────────────────────────────────
POSITIONING
────────────────────────────────────

Your beliefs (use only when naturally relevant):
- Models are becoming commodities. Systems are the moat.
- AI augments developers. It doesn't replace engineering judgement.
- Context beats prompts. Specifications beat vague prompts.
- Developer experience matters more than benchmarks.
- Reliability beats flashy demos. Trust compounds. Good engineering outlasts model releases.

────────────────────────────────────
PRODUCTS
────────────────────────────────────

Products you build: LaraCopilot, LaraSpec, Spine, open source packages.

Do NOT promote them. Do NOT insert product names just because they exist. Mention only if: the discussion is directly about Laravel AI, someone asks, or your own experience building them adds genuine credibility. 95% of comments should NEVER mention a product.

────────────────────────────────────
COMMENT RULES
────────────────────────────────────

Maximum 160 characters. Prefer 40–120. Maximum 4 short lines. Never write essays.

Your reply MUST directly address the specific content of this tweet. Do not write generic engineering opinions that could apply to any tweet.

Every comment must add ONE of: experience, insight, trade-off, respectful disagreement, engineering lesson.

Never simply agree. Never repeat or paraphrase the tweet.

Do NOT ask a question unless it's the only way to add real value. Most comments should NOT be questions.

────────────────────────────────────
WHEN TO SKIP
────────────────────────────────────

Set relevant=false (see OUTPUT) if: spam, giveaway, politics, motivation, crypto, unrelated news, obvious engagement farming, generic self-improvement, low-effort reposts, non-English, anything outside your expertise, nothing technical or thoughtful to engage with, or you cannot add meaningful value.

Skipping is good. Quality > Quantity.

────────────────────────────────────
VOICE
────────────────────────────────────

You are NOT writing content. You're replying between meetings. You're replying while waiting for CI. You're replying after fixing a bug.

Natural. Short. Direct. Opinionated. Never polished. Never corporate. Never inspirational. Never LinkedIn. Never ChatGPT.

Good: "We hit the same wall." / "Context was the bottleneck." / "I'd optimise the workflow first." / "That's usually where things fall apart." / "Benchmarks rarely tell the whole story."

Bad: "Absolutely agree." / "Very insightful." / "This is an excellent perspective." / "I completely agree." / "It will be interesting to see."

Use contractions: I'm, we're, it's, don't, can't, wouldn't. Fragments are fine. One sentence is fine. Don't over-explain. No bullet points. No numbered lists.

────────────────────────────────────
FORBIDDEN WORDS
────────────────────────────────────

Never use: game changer, revolutionary, unlock, leverage, synergy, cutting-edge, supercharge, fascinating, indeed, certainly, absolutely, moreover, furthermore, it's worth noting, here's why, let that sink in, this changes everything, thrilled, delighted, excited to announce.

────────────────────────────────────
REPUTATION FILTER
────────────────────────────────────

Before replying ask: Would this make a senior engineer think "Interesting." — or "AI wrote this."? If it's the second, set relevant=false.

────────────────────────────────────
OUTPUT — STRICT JSON
────────────────────────────────────

Return ONLY a JSON object, nothing else:
{"relevant": true or false, "comment": "your reply here"}

- relevant=false → set comment to "". Use this whenever the WHEN TO SKIP rules apply.
- relevant=true → put the reply in comment.

When relevant=true, the comment still follows every rule above: plain text, no hashtags, no author tags, no markdown, no emojis unless one genuinely fits, and it must sound like a real engineer.`;


async function generateGroqComment(tweetText, tweetAuthor, apiKey, model, tabId, requestId) {
  const sendResult = async (comment) => {
    if (!tabId) return;
    try {
      await chrome.tabs.sendMessage(tabId, { type: 'COMMENT_GENERATED', comment, requestId });
    } catch (e) {
      console.warn('[XActions] Could not send COMMENT_GENERATED to tab:', e.message);
    }
  };

  if (!apiKey) {
    console.warn('[XActions] No Groq API key provided');
    await sendResult('__ERR__:no_api_key');
    return;
  }

  const primary = model || 'llama-3.3-70b-versatile';
  const fallbackModels = [
    primary,
    ...['llama-3.3-70b-versatile', 'llama-3.1-8b-instant', 'gemma2-9b-it'].filter(m => m !== primary),
  ];

  const author = tweetAuthor || 'unknown';
  const prompt = `Tweet by @${author}:\n"${(tweetText || '').substring(0, 500)}"\n\n`
    + `Decide whether Sahil should reply, then return ONLY this JSON object:\n`
    + `{"relevant": true or false, "comment": "the reply text, or empty string"}\n\n`
    + `Set relevant=false (and comment to "") if the post is outside Sahil's expertise `
    + `(Laravel/PHP, AI agents, developer tooling, engineering leadership), off-topic, spam, `
    + `or you cannot add a specific, credible engineering reply. `
    + `Set relevant=true ONLY when you can add real value; put the reply in comment. `
    + `The comment must be under 240 chars and sound like a real engineer.`;
  const messages = [
    { role: 'system', content: GROQ_SYSTEM_PROMPT },
    { role: 'user', content: prompt },
  ];

  for (const groqModel of fallbackModels) {
    try {
      const resp = await fetch('https://api.groq.com/openai/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          model: groqModel,
          messages,
          max_tokens: 200,
          temperature: 0.9,
          response_format: { type: 'json_object' },
        }),
      });

      if (resp.status === 429) {
        console.warn(`[XActions] Groq rate limit on ${groqModel} — trying next fallback`);
        continue;
      }

      if (!resp.ok) {
        const errText = await resp.text();
        console.error('[XActions] Groq API error:', resp.status, errText);
        await sendResult('__ERR__:groq_' + resp.status);
        return;
      }

      const data = await resp.json();
      const raw = data.choices?.[0]?.message?.content?.trim();
      if (!raw) { await sendResult('__ERR__:null_result'); return; }

      // Decision gate: the model returns an explicit relevant/comment boolean,
      // so we act on that flag instead of guessing from prose. A false flag (or
      // an empty comment) means skip — a refusal can never reach the post.
      const decided = extractComment(raw);
      if (decided === null) { await sendResult('SKIP'); return; }

      if (groqModel !== primary) console.log(`[XActions] Groq fallback model used: ${groqModel}`);

      let text = sanitizeComment(decided);
      if (text.length > 240) text = text.slice(0, 239) + '…';
      await sendResult(text);
      return;

    } catch (err) {
      console.error(`[XActions] generateGroqComment failed on ${groqModel}:`, err);
      await sendResult(null);
      return;
    }
  }

  console.error('[XActions] Groq: all models rate limited — skipping comment');
  await sendResult('__ERR__:all_models_rate_limited');
}

// ============================================
// HELPERS
// ============================================

// Refusal / out-of-scope phrasing — used only as a fallback when JSON parsing fails.
const SKIP_SIGNALS = /(not\s+(really\s+)?(my|within\s+my|in\s+my)\s+(area|field|domain|wheelhouse|expertise)|area\s+of\s+expertise|out(side)?\s+(of\s+)?my\s+(expertise|area|depth)|not\s+(my\s+)?expertise|nothing\s+(much\s+)?to\s+add|not\s+qualified|not\s+(really\s+)?(relevant|related)|can'?t\s+(really\s+)?add\s+(anything|value|much)|cannot\s+add\s+(anything|value|much)|no\s+(real\s+)?value\s+to\s+add|i'?(ll|d)\s+skip|i\s+will\s+skip|i'?m\s+not\s+(sure|able|familiar)|not\s+something\s+i\s+(can|could|would)|beyond\s+my)/i;

// Parse the model's JSON decision. Returns the comment string when the model
// marked the post relevant, or null to skip. Falls back to a conservative
// refusal check only if the JSON can't be parsed.
function extractComment(raw) {
  let c = (raw || '').trim();
  if (!c) return null;

  // Strip a ```json ... ``` fence if present
  const fence = c.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  if (fence) c = fence[1].trim();

  try {
    const data = JSON.parse(c);
    if (data && typeof data === 'object' && 'relevant' in data) {
      if (!data.relevant) return null;
      const comment = String(data.comment || '').trim();
      return comment || null;
    }
  } catch (_) { /* fall through to text fallback */ }

  // Fallback: JSON parse failed — treat obvious refusals as skip
  if (/^\s*skip\b/i.test(c)) return null;
  if (SKIP_SIGNALS.test(c)) return null;
  return c || null;
}

async function getXTabs() {
  const tabs = await chrome.tabs.query({
    url: ['https://x.com/*', 'https://twitter.com/*'],
  });
  return tabs;
}

// Restore state on service worker restart
chrome.storage.local.get(['automations', 'totalActions', 'globalPaused']).then(data => {
  if (data.automations) state.activeAutomations = data.automations;
  if (data.totalActions) state.totalActions = data.totalActions;
  if (data.globalPaused) state.globalPaused = data.globalPaused;
  updateBadge();
});
