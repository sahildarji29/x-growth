// XActions Extension — Injected Page Script
// Runs in the actual x.com page context (not content script sandbox)
// Has full access to the page DOM, same as pasting in console
// by nichxbt

(() => {
  if (window.__xactions_injected) return;
  window.__xactions_injected = true;

  // ============================================
  // CORE MODULE (from src/automation/core.js)
  // ============================================
  window.XActions = window.XActions || {};

  window.XActions.Core = (() => {
    const CONFIG = {
      DELAY_SHORT: 1000,
      DELAY_MEDIUM: 5000,
      DELAY_LONG: 15000,
      DELAY_BETWEEN_ACTIONS: 8000,
      MAX_ACTIONS_PER_HOUR: 5,
      MAX_FOLLOWS_PER_DAY: 15,
      MAX_LIKES_PER_DAY: 20,
      MAX_COMMENTS_PER_DAY: 8,
      STORAGE_PREFIX: 'xactions_',
      DEBUG: true,
    };

    const SELECTORS = {
      followButton: '[data-testid$="-follow"]',
      unfollowButton: '[data-testid$="-unfollow"]',
      likeButton: '[data-testid="like"]',
      unlikeButton: '[data-testid="unlike"]',
      retweetButton: '[data-testid="retweet"]',
      replyButton: '[data-testid="reply"]',
      confirmButton: '[data-testid="confirmationSheetConfirm"]',
      tweet: '[data-testid="tweet"]',
      tweetText: '[data-testid="tweetText"]',
      tweetLink: 'a[href*="/status/"]',
      userCell: '[data-testid="UserCell"]',
      userAvatar: '[data-testid="UserAvatar-Container"]',
      userName: '[data-testid="User-Name"]',
      userFollowIndicator: '[data-testid="userFollowIndicator"]',
      tweetInput: '[data-testid="tweetTextarea_0"]',
      searchInput: '[data-testid="SearchBox_Search_Input"]',
      primaryColumn: '[data-testid="primaryColumn"]',
      timeline: 'section[role="region"]',
    };

    const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

    const randomDelay = (min, max) => {
      const delay = Math.floor(Math.random() * (max - min + 1)) + min;
      return sleep(delay);
    };

    const log = (message, type = 'info') => {
      const timestamp = new Date().toLocaleTimeString();
      const prefix = { info: '📘', success: '✅', warning: '⚠️', error: '❌', action: '🔧' }[type] || '📘';
      if (CONFIG.DEBUG || type === 'error') {
        console.log(`${prefix} [${timestamp}] ${message}`);
      }
      // Emit to extension
      notify('ACTION_PERFORMED', { action: `[${type}] ${message}` });
    };

    const scrollToBottom = () => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
    const scrollBy = (pixels) => window.scrollBy({ top: pixels, behavior: 'smooth' });

    const storage = {
      get: (key) => { try { const d = localStorage.getItem(CONFIG.STORAGE_PREFIX + key); return d ? JSON.parse(d) : null; } catch { return null; } },
      set: (key, value) => { try { localStorage.setItem(CONFIG.STORAGE_PREFIX + key, JSON.stringify(value)); return true; } catch { return false; } },
      remove: (key) => localStorage.removeItem(CONFIG.STORAGE_PREFIX + key),
    };

    const waitForElement = async (selector, timeout = 10000) => {
      const start = Date.now();
      while (Date.now() - start < timeout) {
        const el = document.querySelector(selector);
        if (el) return el;
        await sleep(100);
      }
      return null;
    };

    const waitForElements = async (selector, minCount = 1, timeout = 10000) => {
      const start = Date.now();
      while (Date.now() - start < timeout) {
        const els = document.querySelectorAll(selector);
        if (els.length >= minCount) return Array.from(els);
        await sleep(100);
      }
      return [];
    };

    const clickElement = async (element) => {
      if (!element) return false;
      try {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
        await sleep(300);
        element.click();
        return true;
      } catch { return false; }
    };

    const typeText = async (element, text, delay = 50) => {
      if (!element) return false;
      try {
        element.focus();
        for (const char of text) {
          element.textContent += char;
          element.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertText', data: char }));
          await sleep(delay);
        }
        return true;
      } catch { return false; }
    };

    const extractUsername = (element) => {
      const link = element.querySelector('a[href^="/"]');
      if (link) {
        const match = link.getAttribute('href').match(/^\/([^/]+)$/);
        if (match) return match[1].toLowerCase();
      }
      return null;
    };

    const extractTweetInfo = (tweetElement) => {
      try {
        const text = tweetElement.querySelector(SELECTORS.tweetText)?.textContent || '';
        const tweetLink = tweetElement.querySelector(SELECTORS.tweetLink)?.href || '';
        const userName = tweetElement.querySelector(SELECTORS.userName)?.textContent || '';
        return { text, tweetLink, userName };
      } catch { return null; }
    };

    const rateLimit = {
      check: (action, limit, period = 'hour') => {
        const key = `ratelimit_${action}_${period}`;
        const data = storage.get(key) || { count: 0, timestamp: Date.now() };
        const periodMs = period === 'hour' ? 3600000 : 86400000;
        if (Date.now() - data.timestamp > periodMs) { data.count = 0; data.timestamp = Date.now(); }
        return data.count < limit;
      },
      increment: (action, period = 'hour') => {
        const key = `ratelimit_${action}_${period}`;
        const data = storage.get(key) || { count: 0, timestamp: Date.now() };
        data.count++;
        storage.set(key, data);
      },
      getRemaining: (action, limit, period = 'hour') => {
        const key = `ratelimit_${action}_${period}`;
        const data = storage.get(key) || { count: 0, timestamp: Date.now() };
        return Math.max(0, limit - data.count);
      },
    };

    // Notify extension bridge
    function notify(type, data) {
      window.postMessage({ source: 'xactions-page', type, ...data }, '*');
    }

    return {
      CONFIG, SELECTORS, sleep, randomDelay, log,
      scrollToBottom, scrollToTop, scrollBy, storage,
      waitForElement, waitForElements, clickElement, typeText,
      extractUsername, extractTweetInfo, rateLimit, notify,
    };
  })();

  // ============================================
  // AUTOMATION REGISTRY
  // ============================================
  const automationRunners = {};
  const automationStopFlags = {};

  function registerAutomation(id, runFn) {
    automationRunners[id] = runFn;
  }

  // ============================================
  // AUTO-LIKER
  // ============================================
  registerAutomation('autoLiker', async (settings) => {
    const { log, sleep, randomDelay, scrollBy, clickElement, SELECTORS, storage, rateLimit } = window.XActions.Core;
    const opts = {
      LIKE_ALL: false,
      KEYWORDS: settings.keywords || ['AI', 'LLM', 'coding agents', 'agentic AI', 'vibe coding'],
      MAX_LIKES: Math.min(settings.maxActions || 20, 20), // hard cap at 20/day
      MAX_SCROLL_DEPTH: 50,
      MIN_DELAY: settings.minDelay || 8000,
      MAX_DELAY: settings.maxDelay || 35000,
      SKIP_REPLIES: true,
      SKIP_ADS: true,
    };

    let likeCount = 0;
    let scrollCount = 0;
    const likedTweets = new Set(storage.get('liked_tweets') || []);

    const matchesKeywords = (text) => {
      if (opts.KEYWORDS.length === 0) return true;
      const lower = text.toLowerCase();
      return opts.KEYWORDS.some(kw => lower.includes(kw.toLowerCase()));
    };

    log(`🤍 Auto-Liker started (max: ${opts.MAX_LIKES}, keywords: ${opts.KEYWORDS.length ? opts.KEYWORDS.join(', ') : 'any'})`, 'info');

    while (likeCount < opts.MAX_LIKES && scrollCount < opts.MAX_SCROLL_DEPTH && !automationStopFlags['autoLiker']) {
      const tweets = document.querySelectorAll(SELECTORS.tweet);

      for (const tweet of tweets) {
        if (automationStopFlags['autoLiker']) break;
        if (likeCount >= opts.MAX_LIKES) break;
        if (!rateLimit.check('like', 20, 'day')) { log('⚠️ Daily like limit reached (20/day)', 'warning'); return; }

        const tweetLink = tweet.querySelector(SELECTORS.tweetLink)?.href || '';
        const tweetId = tweetLink.match(/status\/(\d+)/)?.[1];
        if (!tweetId || likedTweets.has(tweetId)) continue;

        const text = tweet.querySelector(SELECTORS.tweetText)?.textContent || '';
        if (!matchesKeywords(text)) continue;

        // Skip ads
        if (opts.SKIP_ADS && (tweet.querySelector('[data-testid="placementTracking"]') || tweet.textContent?.includes('Promoted'))) continue;

        const likeBtn = tweet.querySelector(SELECTORS.likeButton);
        if (!likeBtn) continue;

        await clickElement(likeBtn);
        likeCount++;
        likedTweets.add(tweetId);
        rateLimit.increment('like', 'day');

        window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'autoLiker', action: `❤️ Liked tweet ${tweetId} (${likeCount}/${opts.MAX_LIKES})` }, '*');
        log(`❤️ Liked: "${text.substring(0, 50)}..." (${likeCount}/${opts.MAX_LIKES})`, 'success');

        await randomDelay(opts.MIN_DELAY, opts.MAX_DELAY);
      }

      scrollBy(800);
      scrollCount++;
      await sleep(1500);
    }

    storage.set('liked_tweets', Array.from(likedTweets));
    log(`✅ Auto-Liker done! Liked ${likeCount} tweets.`, 'success');
    window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_COMPLETE', automationId: 'autoLiker', summary: `${likeCount} tweets liked` }, '*');
  });

  // ============================================
  // SMART UNFOLLOW
  // ============================================
  registerAutomation('smartUnfollow', async (settings) => {
    const { log, sleep, randomDelay, scrollBy, clickElement, waitForElement, storage, SELECTORS } = window.XActions.Core;
    const opts = {
      DAYS_TO_WAIT: settings.daysToWait || 3,
      MAX_UNFOLLOWS: settings.maxActions || 50,
      WHITELIST: settings.whitelist || [],
      DRY_RUN: settings.dryRun || false,
      DELAY_BETWEEN_UNFOLLOWS: settings.minDelay || 2000,
    };

    let unfollowCount = 0;

    log(`🔄 Smart Unfollow started (max: ${opts.MAX_UNFOLLOWS}, wait: ${opts.DAYS_TO_WAIT}d${opts.DRY_RUN ? ', DRY RUN' : ''})`, 'info');

    // Must be on following page
    const currentPath = window.location.pathname;
    if (!currentPath.endsWith('/following')) {
      log('⚠️ Navigate to your following page first (x.com/YOUR_USERNAME/following)', 'warning');
      window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_ERROR', automationId: 'smartUnfollow', error: 'Navigate to your following page first' }, '*');
      return;
    }

    while (unfollowCount < opts.MAX_UNFOLLOWS && !automationStopFlags['smartUnfollow']) {
      const cells = document.querySelectorAll(SELECTORS.userCell);
      if (cells.length === 0) {
        await sleep(2000);
        scrollBy(600);
        continue;
      }

      for (const cell of cells) {
        if (automationStopFlags['smartUnfollow']) break;
        if (unfollowCount >= opts.MAX_UNFOLLOWS) break;

        const userLink = cell.querySelector('a[href^="/"]');
        const username = userLink?.getAttribute('href')?.replace('/', '') || '';
        if (!username) continue;

        // Check whitelist
        if (opts.WHITELIST.some(w => w.toLowerCase() === username.toLowerCase())) continue;

        // Check if they follow back
        const followsYou = cell.querySelector(SELECTORS.userFollowIndicator);
        if (followsYou) continue; // They follow back, skip

        const unfollowBtn = cell.querySelector(SELECTORS.unfollowButton);
        if (!unfollowBtn) continue;

        if (opts.DRY_RUN) {
          log(`[DRY RUN] Would unfollow @${username}`, 'info');
        } else {
          await clickElement(unfollowBtn);
          await sleep(500);
          const confirmBtn = await waitForElement(SELECTORS.confirmButton, 3000);
          if (confirmBtn) await clickElement(confirmBtn);
        }

        unfollowCount++;
        window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'smartUnfollow', action: `👋 Unfollowed @${username} (${unfollowCount}/${opts.MAX_UNFOLLOWS})` }, '*');
        log(`👋 Unfollowed @${username} (${unfollowCount}/${opts.MAX_UNFOLLOWS})`, 'success');

        await randomDelay(opts.DELAY_BETWEEN_UNFOLLOWS, opts.DELAY_BETWEEN_UNFOLLOWS * 1.5);
      }

      scrollBy(600);
      await sleep(1500);
    }

    log(`✅ Smart Unfollow done! Unfollowed ${unfollowCount} users.`, 'success');
    window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_COMPLETE', automationId: 'smartUnfollow', summary: `${unfollowCount} users unfollowed` }, '*');
  });

  // ============================================
  // KEYWORD FOLLOW
  // ============================================
  registerAutomation('keywordFollow', async (settings) => {
    const { log, sleep, randomDelay, scrollBy, clickElement, waitForElement, storage, rateLimit, SELECTORS } = window.XActions.Core;
    const opts = {
      KEYWORDS: settings.keywords || ['AI automation', 'LLM', 'coding agents', 'agentic AI', 'vibe coding'],
      MAX_FOLLOWS_PER_KEYWORD: settings.maxPerKeyword || 5,
      MAX_FOLLOWS_TOTAL: Math.min(settings.maxActions || 15, 15), // hard cap at 15/day
      MIN_FOLLOWERS: settings.minFollowers || 200,
      MAX_FOLLOWERS: settings.maxFollowers || 50000,
      MUST_HAVE_BIO: settings.mustHaveBio !== false, // default true
      DELAY_BETWEEN_FOLLOWS: settings.minDelay || 60000, // 60s between follows
    };

    let followCount = 0;
    const followedUsers = new Map(Object.entries(storage.get('followed_users') || {}));

    const saveFollowedUser = (username) => {
      followedUsers.set(username.toLowerCase(), { followedAt: Date.now(), followedBack: false });
      storage.set('followed_users', Object.fromEntries(followedUsers));
    };

    log(`🔍 Keyword Follow started (keywords: ${opts.KEYWORDS.join(', ')}, max: ${opts.MAX_FOLLOWS_TOTAL})`, 'info');

    for (const keyword of opts.KEYWORDS) {
      if (followCount >= opts.MAX_FOLLOWS_TOTAL || automationStopFlags['keywordFollow']) break;

      const searchUrl = `https://x.com/search?q=${encodeURIComponent(keyword)}&src=typed_query&f=user`;
      window.location.href = searchUrl;
      await sleep(3000);
      await waitForElement(SELECTORS.userCell, 10000);
      await sleep(1000);

      let keywordFollows = 0;
      let scrollAttempts = 0;

      while (keywordFollows < opts.MAX_FOLLOWS_PER_KEYWORD && followCount < opts.MAX_FOLLOWS_TOTAL && scrollAttempts < 20 && !automationStopFlags['keywordFollow']) {
        const cells = document.querySelectorAll(SELECTORS.userCell);

        for (const cell of cells) {
          if (automationStopFlags['keywordFollow']) break;
          if (keywordFollows >= opts.MAX_FOLLOWS_PER_KEYWORD || followCount >= opts.MAX_FOLLOWS_TOTAL) break;
          if (!rateLimit.check('follow', 15, 'day')) { log('⚠️ Daily follow limit reached (15/day)', 'warning'); return; }

          const userLink = cell.querySelector('a[href^="/"]');
          const username = userLink?.getAttribute('href')?.replace('/', '') || '';
          if (!username || followedUsers.has(username.toLowerCase())) continue;

          // Check for follow button (not already following)
          const followBtn = cell.querySelector(SELECTORS.followButton);
          if (!followBtn) continue;

          await clickElement(followBtn);
          followCount++;
          keywordFollows++;
          rateLimit.increment('follow', 'day');
          saveFollowedUser(username);

          window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'keywordFollow', action: `➕ Followed @${username} for "${keyword}" (${followCount}/${opts.MAX_FOLLOWS_TOTAL})` }, '*');
          log(`➕ Followed @${username} (${followCount}/${opts.MAX_FOLLOWS_TOTAL})`, 'success');

          await randomDelay(opts.DELAY_BETWEEN_FOLLOWS, opts.DELAY_BETWEEN_FOLLOWS * 1.5);
        }

        scrollBy(600);
        scrollAttempts++;
        await sleep(1500);
      }
    }

    log(`✅ Keyword Follow done! Followed ${followCount} users.`, 'success');
    window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_COMPLETE', automationId: 'keywordFollow', summary: `${followCount} users followed` }, '*');
  });

  // ============================================
  // FOLLOW ENGAGERS
  // ============================================
  registerAutomation('followEngagers', async (settings) => {
    const { log, sleep, randomDelay, scrollBy, clickElement, waitForElement, storage, rateLimit, SELECTORS } = window.XActions.Core;
    const opts = {
      MODE: settings.mode || 'likers',
      MAX_FOLLOWS: settings.maxActions || 30,
      MIN_FOLLOWERS: settings.minFollowers || 50,
      DELAY_BETWEEN_FOLLOWS: settings.minDelay || 3000,
    };

    let followCount = 0;

    log(`👥 Follow Engagers started (mode: ${opts.MODE}, max: ${opts.MAX_FOLLOWS})`, 'info');

    // Must be on a tweet page
    if (!window.location.pathname.includes('/status/')) {
      log('⚠️ Navigate to a specific tweet first', 'warning');
      window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_ERROR', automationId: 'followEngagers', error: 'Navigate to a specific tweet first' }, '*');
      return;
    }

    // Click on likes to open likers panel
    const likesLink = document.querySelector('a[href$="/likes"]');
    if (likesLink) {
      await clickElement(likesLink);
      await sleep(2000);
    }

    let scrollAttempts = 0;
    while (followCount < opts.MAX_FOLLOWS && scrollAttempts < 30 && !automationStopFlags['followEngagers']) {
      const cells = document.querySelectorAll(SELECTORS.userCell);

      for (const cell of cells) {
        if (automationStopFlags['followEngagers']) break;
        if (followCount >= opts.MAX_FOLLOWS) break;
        if (!rateLimit.check('follow', 15, 'day')) { log('⚠️ Daily follow limit reached (15/day)', 'warning'); return; }

        const followBtn = cell.querySelector(SELECTORS.followButton);
        if (!followBtn) continue;

        const userLink = cell.querySelector('a[href^="/"]');
        const username = userLink?.getAttribute('href')?.replace('/', '') || 'unknown';

        await clickElement(followBtn);
        followCount++;
        rateLimit.increment('follow', 'day');

        window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'followEngagers', action: `➕ Followed @${username} (${followCount}/${opts.MAX_FOLLOWS})` }, '*');
        log(`➕ Followed engager @${username} (${followCount}/${opts.MAX_FOLLOWS})`, 'success');

        await randomDelay(opts.DELAY_BETWEEN_FOLLOWS, opts.DELAY_BETWEEN_FOLLOWS * 1.5);
      }

      scrollBy(600);
      scrollAttempts++;
      await sleep(1500);
    }

    log(`✅ Follow Engagers done! Followed ${followCount} users.`, 'success');
    window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_COMPLETE', automationId: 'followEngagers', summary: `${followCount} engagers followed` }, '*');
  });

  // ============================================
  // GOD MODE — full growth bot in the browser
  // ============================================
  registerAutomation('godMode', async (settings) => {
    const { log, sleep, randomDelay, clickElement, waitForElement, storage, SELECTORS } = window.XActions.Core;

    const keywords = (settings.keywords || 'AI automation, LLM agents, agentic AI, vibe coding, coding agents')
      .split(',').map(s => s.trim()).filter(Boolean);
    const maxComments  = parseInt(settings.maxComments)  || 50;
    const maxLikes     = parseInt(settings.maxLikes)     || 100;
    const maxFollows   = parseInt(settings.maxFollows)   || 30;
    const commentDelay = Math.max(30, parseInt(settings.commentDelay) || 90) * 1000;
    const groqApiKey   = (settings.groqApiKey || '').trim();
    const groqModel    = (settings.groqModel  || 'llama-3.3-70b-versatile').trim();
    const enableLikes    = settings.enableLikes    !== false;
    const enableComments = settings.enableComments !== false;
    const enableFollows  = settings.enableFollows  !== false;

    let likes = 0, comments = 0, follows = 0;
    const seenIds = new Set(storage.get('godmode_seen') || []);

    const notify = (msg, type = 'info') => {
      log(msg, type);
      window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'godMode', action: msg }, '*');
    };

    // Call Groq API directly via service worker (same as Python growth_bot.py)
    const generateComment = (tweetText, tweetAuthor) => new Promise((resolve) => {
      const requestId = `gm_${Date.now()}`;
      const timer = setTimeout(() => {
        window.removeEventListener('message', handler);
        notify('⚠️ Groq timed out — skipping comment', 'warning');
        resolve(null);
      }, 30000);

      function handler(event) {
        if (event.source !== window) return;
        if (!event.data || event.data.source !== 'xactions-extension') return;
        if (event.data.type !== 'COMMENT_GENERATED' || event.data.requestId !== requestId) return;
        clearTimeout(timer);
        window.removeEventListener('message', handler);
        const c = event.data.comment;
        if (!c || c.startsWith('__ERR__')) { resolve(null); return; }
        if (c === 'SKIP') { resolve('SKIP'); return; }
        resolve(c);
      }

      window.addEventListener('message', handler);
      window.postMessage({
        source: 'xactions-page',
        type: 'GENERATE_COMMENT_GROQ',
        tweetText: tweetText.substring(0, 500),
        tweetAuthor,
        apiKey: groqApiKey,
        model: groqModel,
        requestId,
      }, '*');
    });

    // Post a reply comment on a tweet article element
    const postComment = async (article, text) => {
      const replyBtn = article.querySelector(SELECTORS.replyButton);
      if (!replyBtn) return false;

      await clickElement(replyBtn);
      await sleep(2500);

      const replyBox = await waitForElement(SELECTORS.tweetInput, 8000);
      if (!replyBox) {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        return false;
      }

      replyBox.focus();
      await sleep(200);
      const range = document.createRange();
      range.selectNodeContents(replyBox);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      await sleep(100);
      document.execCommand('insertText', false, text);
      await sleep(800);

      const dialog = replyBox.closest('[role="dialog"]') || document.body;
      const submitBtn =
        dialog.querySelector('[data-testid="tweetButton"]') ||
        dialog.querySelector('[data-testid="tweetButtonInline"]') ||
        document.querySelector('[data-testid="tweetButton"]') ||
        document.querySelector('[data-testid="tweetButtonInline"]');

      if (!submitBtn) {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        return false;
      }

      submitBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
      await sleep(300);
      submitBtn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));

      // Wait for modal to close
      for (let i = 0; i < 25; i++) {
        await sleep(200);
        if (!document.querySelector('[role="dialog"]')) break;
      }
      return true;
    };

    // Follow a user by navigating to their profile and clicking Follow
    const followUser = async (author, returnUrl) => {
      try {
        window.location.href = `https://x.com/${author}`;
        await sleep(3000);

        // Try user-specific testid first, then generic
        let followBtn = document.querySelector(`[data-testid="${author}-follow"]`) ||
                        document.querySelector('[data-testid="follow"]');

        if (!followBtn) {
          // Wait a bit longer for the page to load
          await sleep(2000);
          followBtn = document.querySelector(`[data-testid="${author}-follow"]`) ||
                      document.querySelector('[data-testid="follow"]');
        }

        if (followBtn) {
          await clickElement(followBtn);
          await sleep(1000);
          // Navigate back to search results
          window.location.href = returnUrl;
          await sleep(3000);
          return true;
        }

        window.location.href = returnUrl;
        await sleep(3000);
        return false;
      } catch {
        return false;
      }
    };

    // Collect tweet data from the current page
    const collectTweets = () => {
      const articles = document.querySelectorAll('article[data-testid="tweet"]');
      const results = [];
      for (const article of articles) {
        const linkEl = article.querySelector('a[href*="/status/"]');
        const url = linkEl ? linkEl.href : '';
        const tweetId = (url.match(/status\/(\d+)/) || [])[1];
        if (!tweetId || seenIds.has(tweetId)) continue;

        const text = (article.querySelector('[data-testid="tweetText"]') || {}).innerText || '';
        if (!text.trim()) continue;

        let author = '';
        const avatarEl = article.querySelector('[data-testid^="UserAvatar-Container-"]');
        if (avatarEl) {
          author = avatarEl.getAttribute('data-testid').replace('UserAvatar-Container-', '');
        }
        if (!author) {
          for (const a of article.querySelectorAll('[data-testid="User-Name"] a')) {
            const h = (a.getAttribute('href') || '').replace(/^\//, '').toLowerCase();
            if (/^\w+$/.test(h)) { author = h; break; }
          }
        }

        results.push({ article, tweetId, url, text, author });
      }
      return results;
    };

    notify(`⚡ God Mode started — targets: ${maxLikes} likes, ${maxComments} comments, ${maxFollows} follows`, 'info');

    let kwIndex = 0;

    while (!automationStopFlags['godMode']) {
      // Active hours check (9am–10pm)
      const hour = new Date().getHours();
      if (hour < 9 || hour >= 22) {
        notify('🌙 Outside active hours (9am–10pm). Sleeping 5m.', 'info');
        await sleep(300000);
        continue;
      }

      if (likes >= maxLikes && comments >= maxComments && follows >= maxFollows) {
        notify(`🎉 All targets reached — Likes:${likes} Comments:${comments} Follows:${follows}`, 'success');
        break;
      }

      // Pick next keyword in round-robin
      const kw = keywords[kwIndex % keywords.length];
      kwIndex++;
      notify(`🔍 Searching: "${kw}"`, 'info');

      const searchUrl = `https://x.com/search?q=${encodeURIComponent(kw)}&src=typed_query&f=live`;
      window.location.href = searchUrl;
      await sleep(4000);

      const tweets = collectTweets();
      notify(`📋 Found ${tweets.length} new tweets for "${kw}"`, 'info');

      if (!tweets.length) {
        await sleep(20000);
        continue;
      }

      const authorsToFollow = [];

      for (const tweet of tweets) {
        if (automationStopFlags['godMode']) break;

        // LIKE
        if (enableLikes && likes < maxLikes) {
          const likeBtn = tweet.article.querySelector('[data-testid="like"]');
          if (likeBtn) {
            await clickElement(likeBtn);
            seenIds.add(tweet.tweetId);
            storage.set('godmode_seen', Array.from(seenIds).slice(-500));
            likes++;
            notify(`❤️ Liked @${tweet.author}: "${tweet.text.slice(0, 50)}"`, 'action');
            window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'godMode', action: `❤️ Liked @${tweet.author}` }, '*');
            await randomDelay(3000, 8000);
          }
        }

        // COMMENT
        if (enableComments && comments < maxComments && tweet.text) {
          notify(`🤖 Generating comment for @${tweet.author}...`, 'info');
          const generated = await generateComment(tweet.text, tweet.author);

          if (generated && generated !== 'SKIP') {
            const ok = await postComment(tweet.article, generated);
            if (ok) {
              seenIds.add(tweet.tweetId);
              storage.set('godmode_seen', Array.from(seenIds).slice(-500));
              comments++;
              notify(`💬 Commented on @${tweet.author}: "${generated.slice(0, 60)}..."`, 'success');
              window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'godMode', action: `💬 Commented on @${tweet.author}` }, '*');
              // Human pacing between comments
              notify(`⏳ Waiting ${Math.round(commentDelay / 1000)}s before next comment...`, 'info');
              await sleep(commentDelay);
            }
          } else if (generated === 'SKIP') {
            notify(`⏭️ Skipped @${tweet.author} (off-topic)`, 'info');
          }
        }

        // Queue for follow (done after tweet loop to avoid stale page state)
        if (enableFollows && follows < maxFollows && tweet.author) {
          authorsToFollow.push(tweet.author);
        }

        if (automationStopFlags['godMode']) break;
      }

      // FOLLOW — visit each author's profile
      if (enableFollows && authorsToFollow.length) {
        for (const author of authorsToFollow) {
          if (automationStopFlags['godMode'] || follows >= maxFollows) break;
          const ok = await followUser(author, searchUrl);
          if (ok) {
            follows++;
            notify(`➕ Followed @${author} (${follows}/${maxFollows})`, 'success');
            window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'godMode', action: `➕ Followed @${author}` }, '*');
            await randomDelay(5000, 12000);
          }
        }
      }

      notify(`📊 Cycle done — Likes:${likes}/${maxLikes} Comments:${comments}/${maxComments} Follows:${follows}/${maxFollows}`, 'info');

      if (!automationStopFlags['godMode']) {
        await randomDelay(15000, 30000);
      }
    }

    notify(`⚡ God Mode stopped — Likes:${likes} Comments:${comments} Follows:${follows}`, 'info');
    window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_COMPLETE', automationId: 'godMode', summary: `${likes} likes · ${comments} comments · ${follows} follows` }, '*');
  });

  // ============================================
  // AUTO-COMMENTER
  // ============================================
  registerAutomation('autoCommenter', async (settings) => {
    const { log, sleep, scrollToTop, clickElement, waitForElement, storage, SELECTORS } = window.XActions.Core;

    // Fetch the logged-in user's profile once per hour (uses page's own cookies)
    async function getMyProfile() {
      const cached = storage.get('my_profile');
      if (cached && cached.handle && Date.now() - cached.fetchedAt < 3600000) return cached;

      // Primary: get handle from the nav profile link — works instantly, no API needed
      let navHandle = '';
      const profileNavLink = document.querySelector('a[data-testid="AppTabBar_Profile_Link"]');
      if (profileNavLink) {
        const m = (profileNavLink.getAttribute('href') || '').match(/^\/(\w+)$/);
        if (m) navHandle = m[1].toLowerCase();
      }

      try {
        const ct0 = document.cookie.match(/ct0=([^;]+)/)?.[1] || '';
        const headers = {
          'Authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
          'x-csrf-token': ct0,
          'x-twitter-auth-type': 'OAuth2Session',
          'x-twitter-client-language': 'en',
        };

        const creds = await fetch(
          'https://api.twitter.com/1.1/account/verify_credentials.json?include_entities=false&skip_status=false',
          { credentials: 'include', headers }
        ).then(r => r.json());

        const handle = (creds.screen_name || navHandle || '').toLowerCase();
        const name   = creds.name || handle;
        const bio    = creds.description || '';

        // Fetch last 5 original tweets for voice/style context
        let recentTweets = [];
        try {
          const tl = await fetch(
            `https://api.twitter.com/1.1/statuses/user_timeline.json?screen_name=${handle}&count=5&tweet_mode=extended&exclude_replies=true&include_rts=false`,
            { credentials: 'include', headers }
          ).then(r => r.json());
          recentTweets = Array.isArray(tl)
            ? tl.map(t => (t.full_text || t.text || '').replace(/https?:\/\/\S+/g, '').trim()).filter(Boolean)
            : [];
        } catch { /* timeline optional */ }

        const profile = { handle, name, bio, recentTweets, fetchedAt: Date.now() };
        storage.set('my_profile', profile);
        log(`👤 Profile loaded: @${handle}${bio ? ` — "${bio.substring(0, 60)}..."` : ''}`, 'info');
        return profile;
      } catch {
        // Fallback: use the nav handle we already have
        const handle = navHandle;
        log(`⚠️ Profile API failed — using nav handle (@${handle || 'unknown'})`, 'warning');
        return { handle, name: '', bio: '', recentTweets: [], fetchedAt: Date.now() };
      }
    }

    const opts = {
      FALLBACK_COMMENTS: (settings.comments && settings.comments.length > 0) ? settings.comments : [
        'This is the direction AI development needs to go.',
        'Solid take — the tooling around this is evolving fast.',
        'Exactly the kind of thinking the space needs more of.',
        'Been following this closely. The implications are huge.',
        'Great framing. The agent-native approach changes everything.',
        'This is what gets overlooked in most AI discussions.',
        'The workflow implications here are significant.',
      ],
      CHECK_INTERVAL_SECONDS: settings.checkInterval || 60,
      MAX_COMMENTS: Math.min(settings.maxActions || 5, 8), // hard cap at 8/day
      KEYWORDS: settings.keywords || ['AI', 'LLM', 'agentic', 'coding agents', 'vibe coding'],
      CLAUDE_TIMEOUT_MS: Math.max(30, Math.min(180, Number(settings.claudeTimeout) || 90)) * 1000,
    };

    let commentCount = 0;
    const commentedTweets = new Set(storage.get('commented_tweets') || []);

    // Generic fallback — only used when Claude is completely unavailable
    const getRandomFallback = () => opts.FALLBACK_COMMENTS[Math.floor(Math.random() * opts.FALLBACK_COMMENTS.length)];

    // Tweet-aware fallback — extracts a meaningful word from the tweet so the comment at least references it
    const getTweetFallback = (tweetText) => {
      const stopWords = new Set(['this','that','with','have','from','they','will','been','were','when','what','your','their','about','which','there','would','could','should','into','also','more','very','just','like','some','than','then','these','those','such','only','both','most','over','after','where','while','before','through','during','other','each','many','much','being','might','shall','upon','even','still','well']);
      const words = tweetText.split(/\s+/)
        .map(w => w.replace(/[^a-zA-Z0-9]/g, ''))
        .filter(w => w.length > 4 && !stopWords.has(w.toLowerCase()) && !/^https?/.test(w));
      const pick = words[Math.floor(Math.random() * Math.min(words.length, 8))] || '';
      if (!pick) return getRandomFallback();
      const templates = [
        `The ${pick} piece is what makes this worth paying attention to.`,
        `Exactly — ${pick} is the part most people overlook.`,
        `This hits on ${pick} in a way I haven't seen framed this clearly before.`,
      ];
      return templates[Math.floor(Math.random() * templates.length)];
    };

    const generateComment = (tweetText, userProfile) => new Promise((resolve) => {
      const requestId = `cmt_${Date.now()}`;
      // Outer timer = configured timeout + 15s buffer for navigation/typing overhead
      const outerTimeoutMs = opts.CLAUDE_TIMEOUT_MS + 15000;
      const timer = setTimeout(() => {
        window.removeEventListener('message', handler);
        log(`⚠️ Claude timed out (${Math.round(outerTimeoutMs / 1000)}s) — skipping comment`, 'warning');
        resolve(null);
      }, outerTimeoutMs);

      function handler(event) {
        if (event.source !== window) return;
        if (!event.data || event.data.source !== 'xactions-extension') return;
        if (event.data.type !== 'COMMENT_GENERATED') return;
        if (event.data.requestId !== requestId) return;
        clearTimeout(timer);
        window.removeEventListener('message', handler);
        const c = event.data.comment;
        if (!c) {
          log('⚠️ Claude: no response (is claude.ai open in a tab?)', 'warning');
          resolve(null);
        } else if (c === 'SKIP') {
          log('⏭️ Post not in niche — skipping', 'info');
          resolve('SKIP');
        } else if (c.startsWith('__ERR__')) {
          // Surface the exact API error in the popup log
          const code = c.replace('__ERR__:', '');
          log(`⚠️ Claude API error: ${code}`, 'warning');
          resolve(null);
        } else {
          log(`🤖 AI comment: "${c.substring(0, 60)}..."`, 'info');
          resolve(c);
        }
      }

      window.addEventListener('message', handler);
      window.postMessage({
        source: 'xactions-page',
        type: 'GENERATE_COMMENT',
        tweetText: tweetText.substring(0, 500),
        userProfile,
        requestId,
      }, '*');
    });

    const closeReplyModal = () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    };

    log(`💬 Auto-Commenter started (AI-powered, max: ${opts.MAX_COMMENTS}, interval: ${opts.CHECK_INTERVAL_SECONDS}s)`, 'info');

    // Load profile once at startup — used for every comment's persona context
    const myProfile = await getMyProfile();

    while (commentCount < opts.MAX_COMMENTS && !automationStopFlags['autoCommenter']) {
      scrollToTop();
      await sleep(2000);

      const tweets = document.querySelectorAll(SELECTORS.tweet);
      let postedThisCycle = false;

      for (const tweet of tweets) {
        if (automationStopFlags['autoCommenter']) break;
        if (commentCount >= opts.MAX_COMMENTS) break;

        const tweetLink = tweet.querySelector(SELECTORS.tweetLink)?.href || '';
        const tweetId = tweetLink.match(/status\/(\d+)/)?.[1];
        if (!tweetId || commentedTweets.has(tweetId)) continue;

        // Skip tweets authored by the logged-in user (avoids commenting on own posts/replies)
        if (myProfile?.handle) {
          // Twitter embeds the handle directly in data-testid="UserAvatar-Container-{handle}"
          const avatarEl = tweet.querySelector('[data-testid^="UserAvatar-Container-"]');
          const testId = avatarEl?.getAttribute('data-testid') || '';
          let tweetAuthorHandle = testId.startsWith('UserAvatar-Container-')
            ? testId.slice('UserAvatar-Container-'.length).toLowerCase()
            : '';

          // Fallback: extract from User-Name anchor hrefs
          if (!tweetAuthorHandle) {
            for (const a of tweet.querySelectorAll('[data-testid="User-Name"] a')) {
              const h = (a.getAttribute('href') || '').replace(/^\//, '').toLowerCase();
              if (/^\w+$/.test(h)) { tweetAuthorHandle = h; break; }
            }
          }

          if (tweetAuthorHandle && tweetAuthorHandle === myProfile.handle) {
            if (tweetId) commentedTweets.add(tweetId);
            storage.set('commented_tweets', Array.from(commentedTweets));
            log(`⏭️ Skipping own tweet (@${tweetAuthorHandle})`, 'info');
            continue;
          }
        }

        const text = tweet.querySelector(SELECTORS.tweetText)?.textContent || '';
        if (opts.KEYWORDS.length > 0 && !opts.KEYWORDS.some(kw => text.toLowerCase().includes(kw.toLowerCase()))) continue;

        const replyBtn = tweet.querySelector(SELECTORS.replyButton);
        if (!replyBtn) continue;

        // Step 1: generate comment first (before touching the UI)
        log(`🤖 Generating comment for: "${text.substring(0, 60)}..."`, 'info');
        const generated = await generateComment(text, myProfile);

        // Claude decided this post is off-topic — skip without commenting
        if (generated === 'SKIP') {
          commentedTweets.add(tweetId);
          storage.set('commented_tweets', Array.from(commentedTweets));
          log('⏭️ Skipping post (not in niche)', 'info');
          continue;
        }

        if (!generated) {
          log('⚠️ Claude did not respond. Make sure claude.ai is open in a browser tab.', 'warning');
          break; // Claude is unavailable for the whole cycle — no point checking more tweets
        }
        const safeComment = generated;

        // Step 2: open reply modal
        await clickElement(replyBtn);
        await sleep(2500); // wait for modal to fully render

        // Step 3: find the reply textarea
        const replyBox = await waitForElement(SELECTORS.tweetInput, 8000);
        if (!replyBox) {
          log('⚠️ Reply box not found — stopping cycle', 'warning');
          closeReplyModal();
          await sleep(500);
          break; // don't touch other tweets while a modal may be open
        }

        // Step 4: precisely select only this element's content, then replace it
        replyBox.focus();
        await sleep(200);
        const selRange = document.createRange();
        selRange.selectNodeContents(replyBox);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(selRange);
        await sleep(100);
        document.execCommand('insertText', false, safeComment);
        await sleep(800);

        // Step 5: find submit button scoped to the reply dialog
        const replyDialog = replyBox.closest('[role="dialog"]') || document.body;
        const submitBtn =
          replyDialog.querySelector('[data-testid="tweetButton"]') ||
          replyDialog.querySelector('[data-testid="tweetButtonInline"]') ||
          document.querySelector('[data-testid="tweetButtonInline"]') ||
          document.querySelector('[data-testid="tweetButton"]');

        if (!submitBtn) {
          log('⚠️ Reply submit button not found — stopping cycle', 'warning');
          closeReplyModal();
          await sleep(500);
          break; // stop cycle; don't carry over open modal to next tweet
        }

        // Click via MouseEvent (more reliable than .click() for React buttons)
        submitBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
        await sleep(300);
        submitBtn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));

        // Wait for the modal to actually close (poll for dialog gone, up to 5s)
        let closed = false;
        for (let i = 0; i < 25; i++) {
          await sleep(200);
          if (!document.querySelector('[role="dialog"]')) { closed = true; break; }
        }
        if (!closed) log('⚠️ Reply modal did not close after submit', 'warning');

        commentCount++;
        commentedTweets.add(tweetId);
        storage.set('commented_tweets', Array.from(commentedTweets));
        postedThisCycle = true;

        window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'autoCommenter', action: `💬 Commented "${safeComment.substring(0, 60)}..." on tweet ${tweetId} (${commentCount}/${opts.MAX_COMMENTS})` }, '*');
        log(`💬 Posted: "${safeComment}" (${commentCount}/${opts.MAX_COMMENTS})`, 'success');

        // ONE comment per cycle — break out and wait before finding the next post
        break;
      }

      if (automationStopFlags['autoCommenter'] || commentCount >= opts.MAX_COMMENTS) break;

      log(`⏱️ ${postedThisCycle ? 'Comment posted.' : 'No matching tweet found.'} Waiting ${opts.CHECK_INTERVAL_SECONDS}s...`, 'info');
      await sleep(opts.CHECK_INTERVAL_SECONDS * 1000);
    }

    log(`✅ Auto-Commenter done! Posted ${commentCount} comments.`, 'success');
    window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_COMPLETE', automationId: 'autoCommenter', summary: `${commentCount} comments posted` }, '*');
  });

  // ============================================
  // GROWTH SUITE
  // ============================================
  registerAutomation('growthSuite', async (settings) => {
    const { log, sleep, randomDelay, scrollBy, clickElement, waitForElement, storage, rateLimit, SELECTORS } = window.XActions.Core;
    const opts = {
      KEYWORDS: settings.keywords || ['AI automation', 'LLM', 'agentic AI', 'vibe coding', 'coding agents'],
      enableFollow: settings.enableFollow !== false,
      enableLike: settings.enableLike !== false,
      enableUnfollow: settings.enableUnfollow !== false,
      maxFollows: Math.min(settings.maxFollows || 15, 15),
      maxLikes: Math.min(settings.maxLikes || 20, 20),
      maxUnfollows: Math.min(settings.maxUnfollows || 15, 15),
      UNFOLLOW_AFTER_DAYS: settings.daysToWait || 3,
      SESSION_DURATION_MINUTES: settings.sessionMinutes || 30,
      DELAY: settings.minDelay || 8000,
    };

    const endTime = Date.now() + opts.SESSION_DURATION_MINUTES * 60 * 1000;
    const state = { follows: 0, likes: 0, unfollows: 0 };

    log(`🚀 Growth Suite started! Session: ${opts.SESSION_DURATION_MINUTES}min`, 'info');
    log(`   Follow: ${opts.enableFollow}, Like: ${opts.enableLike}, Unfollow: ${opts.enableUnfollow}`, 'info');

    // Phase 1: Like tweets in feed
    if (opts.enableLike && !automationStopFlags['growthSuite']) {
      log('📌 Phase 1: Liking tweets in feed...', 'action');
      let scrolls = 0;
      while (state.likes < opts.maxLikes && scrolls < 30 && Date.now() < endTime && !automationStopFlags['growthSuite']) {
        const tweets = document.querySelectorAll(SELECTORS.tweet);
        for (const tweet of tweets) {
          if (automationStopFlags['growthSuite'] || state.likes >= opts.maxLikes || Date.now() >= endTime) break;
          const text = tweet.querySelector(SELECTORS.tweetText)?.textContent || '';
          if (opts.KEYWORDS.length > 0 && !opts.KEYWORDS.some(kw => text.toLowerCase().includes(kw.toLowerCase()))) continue;
          const likeBtn = tweet.querySelector(SELECTORS.likeButton);
          if (!likeBtn) continue;
          await clickElement(likeBtn);
          state.likes++;
          window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'growthSuite', action: `❤️ Liked tweet (${state.likes}/${opts.maxLikes})` }, '*');
          await randomDelay(opts.DELAY, opts.DELAY * 1.5);
        }
        scrollBy(600);
        scrolls++;
        await sleep(1500);
      }
    }

    log(`✅ Growth Suite done! Likes: ${state.likes}, Follows: ${state.follows}, Unfollows: ${state.unfollows}`, 'success');
    window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_COMPLETE', automationId: 'growthSuite', summary: `${state.likes} likes, ${state.follows} follows, ${state.unfollows} unfollows` }, '*');
  });

  // ============================================
  // ACCOUNT INFO SCRAPER
  // ============================================
  function scrapeAccountInfo() {
    try {
      // Try to get account info from the page
      const nameEl = document.querySelector('[data-testid="UserName"]') || document.querySelector('div[dir="ltr"] > span');
      const avatarEl = document.querySelector('[data-testid="UserAvatar-Container"] img') || document.querySelector('img[alt][src*="profile_images"]');

      // Try nav sidebar for logged-in user
      const sidebarAvatar = document.querySelector('div[data-testid="SideNav_AccountSwitcher_Button"] img');
      const sidebarName = document.querySelector('div[data-testid="SideNav_AccountSwitcher_Button"]');

      return {
        name: nameEl?.textContent || sidebarName?.querySelector('span')?.textContent || 'Unknown',
        avatar: avatarEl?.src || sidebarAvatar?.src || '',
        handle: window.location.pathname.split('/')[1] || '',
        url: window.location.href,
      };
    } catch {
      return { name: 'Unknown', avatar: '', handle: '', url: window.location.href };
    }
  }

  // ============================================
  // MESSAGE LISTENER (from content script bridge)
  // ============================================
  window.addEventListener('message', async (event) => {
    if (event.source !== window) return;
    if (!event.data || event.data.source !== 'xactions-extension') return;

    const msg = event.data;

    switch (msg.type) {
      case 'RUN_AUTOMATION': {
        const runner = automationRunners[msg.automationId];
        if (runner) {
          automationStopFlags[msg.automationId] = false;
          try {
            await runner(msg.settings || {});
          } catch (err) {
            console.error(`XActions automation error (${msg.automationId}):`, err);
            window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_ERROR', automationId: msg.automationId, error: err.message }, '*');
          }
        }
        break;
      }

      case 'STOP_AUTOMATION':
        automationStopFlags[msg.automationId] = true;
        break;

      case 'STOP_ALL':
      case 'PAUSE_ALL':
        Object.keys(automationStopFlags).forEach(k => automationStopFlags[k] = true);
        Object.keys(automationRunners).forEach(k => automationStopFlags[k] = true);
        break;

      case 'RESUME_ALL':
        // Resuming would need re-running, handled by popup
        break;

      case 'GET_ACCOUNT_INFO':
        window.postMessage({ source: 'xactions-page', type: 'ACCOUNT_INFO', data: scrapeAccountInfo() }, '*');
        break;
    }
  });

  console.log('✅ XActions automation engine injected');

  // ============================================
  // VIDEO DOWNLOADER
  // ============================================
  registerAutomation('videoDownloader', async (settings) => {
    const { log, sleep, SELECTORS } = window.XActions.Core;
    const opts = {
      quality: settings.quality || 'highest',
      autoDownload: settings.autoDownload || false,
      showButton: settings.showButton !== false,
    };

    log('🎬 Video Downloader enabled — scanning for videos...', 'info');

    const injectedTweets = new Set();

    function findVideoUrl(tweetEl) {
      // Look for video elements
      const video = tweetEl.querySelector('video');
      if (video?.src) return video.src;
      // Check for source tags
      const source = tweetEl.querySelector('video source');
      if (source?.src) return source.src;
      // Try to find mp4 URL in tweet data
      const links = tweetEl.querySelectorAll('a[href*="video"]');
      for (const link of links) {
        if (link.href.includes('.mp4')) return link.href;
      }
      return null;
    }

    function addDownloadButton(tweetEl) {
      const tweetLink = tweetEl.querySelector(SELECTORS.tweetLink)?.href || '';
      const tweetId = tweetLink.match(/status\/(\d+)/)?.[1];
      if (!tweetId || injectedTweets.has(tweetId)) return;

      // Check for video content
      const hasVideo = tweetEl.querySelector('video') || tweetEl.querySelector('[data-testid="videoPlayer"]') || tweetEl.querySelector('[data-testid="videoComponent"]');
      if (!hasVideo) return;

      injectedTweets.add(tweetId);
      const actionBar = tweetEl.querySelector('[role="group"]');
      if (!actionBar) return;

      const btn = document.createElement('button');
      btn.className = 'xactions-dl-btn';
      btn.innerHTML = '⬇';
      btn.title = 'Download video (XActions)';
      btn.style.cssText = 'background:none;border:none;cursor:pointer;font-size:16px;padding:4px 8px;border-radius:50%;transition:background 0.2s;color:#1d9bf0;';
      btn.addEventListener('mouseenter', () => btn.style.background = 'rgba(29,155,240,0.1)');
      btn.addEventListener('mouseleave', () => btn.style.background = 'none');

      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        btn.innerHTML = '⏳';
        const videoUrl = findVideoUrl(tweetEl);
        if (videoUrl) {
          const a = document.createElement('a');
          a.href = videoUrl;
          a.download = `xactions_video_${tweetId}.mp4`;
          a.click();
          btn.innerHTML = '✅';
          window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'videoDownloader', action: `⬇ Downloaded video from tweet ${tweetId}` }, '*');
          log(`⬇ Downloaded video from tweet ${tweetId}`, 'success');
        } else {
          // Fallback: open tweet in new tab for manual download
          btn.innerHTML = '🔗';
          btn.title = 'Could not extract video URL — try right-click > Save Video';
          log(`⚠️ Could not extract video URL for tweet ${tweetId}`, 'warning');
        }
        setTimeout(() => { btn.innerHTML = '⬇'; btn.title = 'Download video (XActions)'; }, 3000);
      });

      actionBar.appendChild(btn);
    }

    if (opts.showButton) {
      // Initial scan
      document.querySelectorAll(SELECTORS.tweet).forEach(addDownloadButton);

      // Watch for new tweets
      const observer = new MutationObserver(() => {
        document.querySelectorAll(SELECTORS.tweet).forEach(addDownloadButton);
      });
      observer.observe(document.body, { childList: true, subtree: true });

      // Store observer so we can disconnect on stop
      window.__xactions_videoObserver = observer;

      // Keep running until stopped
      while (!automationStopFlags['videoDownloader']) {
        await sleep(5000);
      }
      observer.disconnect();
      window.__xactions_videoObserver = null;
    }

    log('🎬 Video Downloader disabled', 'info');
    window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_COMPLETE', automationId: 'videoDownloader', summary: 'Video downloader stopped' }, '*');
  });

  // ============================================
  // UNFOLLOWER DETECTOR
  // ============================================
  registerAutomation('unfollowerDetector', async (settings) => {
    const { log, sleep, scrollBy, waitForElements, SELECTORS, storage } = window.XActions.Core;
    const opts = {
      notifications: settings.notifications !== false,
      keepHistory: settings.keepHistory !== false,
    };

    log('🔔 Unfollower Detector scanning...', 'info');

    // Get current path to determine username
    const pathParts = window.location.pathname.split('/');
    const username = pathParts[1];
    if (!username || pathParts[2] !== 'followers') {
      log('⚠️ Navigate to your followers page first (x.com/YOUR_USERNAME/followers)', 'warning');
      window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_ERROR', automationId: 'unfollowerDetector', error: 'Navigate to your followers page (x.com/YOUR_USERNAME/followers)' }, '*');
      return;
    }

    const currentFollowers = new Set();
    let scrollAttempts = 0;
    let lastCount = 0;
    let noNewCount = 0;

    // Scroll through followers page and collect
    while (scrollAttempts < 100 && noNewCount < 5 && !automationStopFlags['unfollowerDetector']) {
      const cells = document.querySelectorAll(SELECTORS.userCell);
      cells.forEach(cell => {
        const userLink = cell.querySelector('a[href^="/"]');
        const handle = userLink?.getAttribute('href')?.replace('/', '');
        if (handle) currentFollowers.add(handle.toLowerCase());
      });

      if (currentFollowers.size === lastCount) {
        noNewCount++;
      } else {
        noNewCount = 0;
        lastCount = currentFollowers.size;
      }

      scrollBy(800);
      scrollAttempts++;
      await sleep(1000);
    }

    log(`📋 Found ${currentFollowers.size} current followers`, 'info');

    // Compare with previous snapshot
    const previousFollowers = new Set(storage.get('follower_snapshot') || []);
    const unfollowers = [];

    if (previousFollowers.size > 0) {
      for (const prev of previousFollowers) {
        if (!currentFollowers.has(prev)) {
          unfollowers.push(prev);
        }
      }
      const newFollowers = [];
      for (const curr of currentFollowers) {
        if (!previousFollowers.has(curr)) {
          newFollowers.push(curr);
        }
      }

      if (unfollowers.length > 0) {
        log(`🚨 ${unfollowers.length} unfollower(s): ${unfollowers.map(u => '@' + u).join(', ')}`, 'warning');
        window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'unfollowerDetector', action: `🚨 Detected ${unfollowers.length} unfollower(s): ${unfollowers.map(u => '@' + u).join(', ')}` }, '*');
      } else {
        log('✅ No unfollowers detected since last scan', 'success');
      }
      if (newFollowers.length > 0) {
        log(`🎉 ${newFollowers.length} new follower(s): ${newFollowers.map(u => '@' + u).join(', ')}`, 'success');
      }

      // Save unfollower history
      if (opts.keepHistory && unfollowers.length > 0) {
        const history = storage.get('unfollower_history') || [];
        history.unshift({ date: Date.now(), unfollowers, newFollowers });
        if (history.length > 50) history.length = 50;
        storage.set('unfollower_history', history);
      }
    } else {
      log('📸 First scan — saving follower snapshot for future comparison', 'info');
    }

    // Save current snapshot
    storage.set('follower_snapshot', Array.from(currentFollowers));
    storage.set('last_follower_scan', Date.now());

    log(`✅ Unfollower scan complete! ${currentFollowers.size} followers recorded`, 'success');
    window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_COMPLETE', automationId: 'unfollowerDetector', summary: `${unfollowers.length} unfollower(s), ${currentFollowers.size} total followers` }, '*');
  });

  // ============================================
  // BEST TIME TO POST
  // ============================================
  registerAutomation('bestTimeToPost', async (settings) => {
    const { log, sleep, scrollBy, SELECTORS, storage } = window.XActions.Core;
    const opts = {
      tweetCount: settings.tweetCount || 50,
      timezone: settings.timezone || 'local',
    };

    log(`📊 Best Time to Post — analyzing ${opts.tweetCount} tweets...`, 'info');

    const tweetData = [];
    let scrollAttempts = 0;
    let noNewCount = 0;
    let lastCount = 0;
    const seenTweets = new Set();

    while (tweetData.length < opts.tweetCount && scrollAttempts < 80 && noNewCount < 5 && !automationStopFlags['bestTimeToPost']) {
      const tweets = document.querySelectorAll(SELECTORS.tweet);

      for (const tweet of tweets) {
        if (tweetData.length >= opts.tweetCount) break;
        const link = tweet.querySelector(SELECTORS.tweetLink)?.href || '';
        const tweetId = link.match(/status\/(\d+)/)?.[1];
        if (!tweetId || seenTweets.has(tweetId)) continue;
        seenTweets.add(tweetId);

        // Extract engagement data
        const text = tweet.querySelector(SELECTORS.tweetText)?.textContent || '';
        const timeEl = tweet.querySelector('time');
        const datetime = timeEl?.getAttribute('datetime');
        if (!datetime) continue;

        // Count likes, retweets, replies from aria-labels
        const groups = tweet.querySelectorAll('[role="group"] button');
        let likes = 0, retweets = 0, replies = 0;
        groups.forEach(btn => {
          const label = btn.getAttribute('aria-label') || '';
          const likeMatch = label.match(/(\d+)\s*[Ll]ike/);
          const rtMatch = label.match(/(\d+)\s*[Rr]e(?:post|tweet)/);
          const replyMatch = label.match(/(\d+)\s*repl/i);
          if (likeMatch) likes = parseInt(likeMatch[1]);
          if (rtMatch) retweets = parseInt(rtMatch[1]);
          if (replyMatch) replies = parseInt(replyMatch[1]);
        });

        const date = new Date(datetime);
        tweetData.push({
          tweetId,
          datetime,
          hour: date.getHours(),
          day: date.getDay(),
          dayName: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][date.getDay()],
          likes,
          retweets,
          replies,
          engagement: likes + retweets + replies,
          text: text.substring(0, 80),
        });

        window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'bestTimeToPost', action: `📊 Analyzed tweet ${tweetData.length}/${opts.tweetCount}` }, '*');
      }

      if (tweetData.length === lastCount) {
        noNewCount++;
      } else {
        noNewCount = 0;
        lastCount = tweetData.length;
      }

      scrollBy(800);
      scrollAttempts++;
      await sleep(1000);
    }

    // Compute best times
    const hourStats = {};
    const dayStats = {};
    for (let h = 0; h < 24; h++) hourStats[h] = { total: 0, count: 0 };
    for (let d = 0; d < 7; d++) dayStats[d] = { total: 0, count: 0 };

    for (const t of tweetData) {
      hourStats[t.hour].total += t.engagement;
      hourStats[t.hour].count++;
      dayStats[t.day].total += t.engagement;
      dayStats[t.day].count++;
    }

    const bestHours = Object.entries(hourStats)
      .filter(([, v]) => v.count > 0)
      .map(([h, v]) => ({ hour: parseInt(h), avg: v.total / v.count, count: v.count }))
      .sort((a, b) => b.avg - a.avg);

    const bestDays = Object.entries(dayStats)
      .filter(([, v]) => v.count > 0)
      .map(([d, v]) => ({ day: parseInt(d), dayName: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d], avg: v.total / v.count, count: v.count }))
      .sort((a, b) => b.avg - a.avg);

    // Save results
    const results = { tweetData, bestHours, bestDays, analyzedAt: Date.now(), tweetCount: tweetData.length };
    storage.set('best_time_results', results);

    if (bestHours.length > 0) {
      const top3 = bestHours.slice(0, 3).map(h => `${h.hour}:00 (avg ${h.avg.toFixed(1)} eng)`).join(', ');
      log(`🏆 Best hours: ${top3}`, 'success');
    }
    if (bestDays.length > 0) {
      const topDay = bestDays[0];
      log(`📅 Best day: ${topDay.dayName} (avg ${topDay.avg.toFixed(1)} engagement)`, 'success');
    }

    log(`✅ Analyzed ${tweetData.length} tweets!`, 'success');
    window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_COMPLETE', automationId: 'bestTimeToPost', summary: `${tweetData.length} tweets analyzed` }, '*');
  });

  // ============================================
  // THREAD READER
  // ============================================
  registerAutomation('threadReader', async (settings) => {
    const { log, sleep, SELECTORS } = window.XActions.Core;
    const opts = {
      showUnrollBtn: settings.showUnrollBtn !== false,
      autoDetect: settings.autoDetect !== false,
      maxTweets: settings.maxTweets || 50,
    };

    log('🧵 Thread Reader enabled — watching for threads...', 'info');

    const processedThreads = new Set();

    function isThread(tweetEl) {
      // Thread indicators: "Show this thread" link, self-reply chain, thread line
      const showThread = tweetEl.querySelector('a[href*="/status/"]')?.textContent?.includes('Show this thread');
      const threadLine = tweetEl.querySelector('[data-testid="tweet-thread-line"]') || tweetEl.parentElement?.querySelector('[style*="border-left"]');
      return showThread || threadLine;
    }

    function addUnrollButton(tweetEl) {
      const tweetLink = tweetEl.querySelector(SELECTORS.tweetLink)?.href || '';
      const tweetId = tweetLink.match(/status\/(\d+)/)?.[1];
      if (!tweetId || processedThreads.has(tweetId)) return;
      if (!isThread(tweetEl)) return;

      processedThreads.add(tweetId);

      const actionBar = tweetEl.querySelector('[role="group"]');
      if (!actionBar) return;

      const btn = document.createElement('button');
      btn.className = 'xactions-unroll-btn';
      btn.innerHTML = '🧵';
      btn.title = 'Unroll thread (XActions)';
      btn.style.cssText = 'background:none;border:none;cursor:pointer;font-size:14px;padding:4px 8px;border-radius:50%;transition:background 0.2s;color:#1d9bf0;';
      btn.addEventListener('mouseenter', () => btn.style.background = 'rgba(29,155,240,0.1)');
      btn.addEventListener('mouseleave', () => btn.style.background = 'none');

      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        btn.innerHTML = '⏳';

        // Collect thread tweets by navigating to the tweet
        const threadTweets = [];
        const currentTweets = document.querySelectorAll(SELECTORS.tweet);
        currentTweets.forEach(t => {
          const text = t.querySelector(SELECTORS.tweetText)?.textContent || '';
          if (text) threadTweets.push(text);
        });

        if (threadTweets.length > 0) {
          // Create overlay with unrolled thread
          const overlay = document.createElement('div');
          overlay.id = 'xactions-thread-overlay';
          overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:99999;overflow-y:auto;padding:20px;';

          const container = document.createElement('div');
          container.style.cssText = 'max-width:600px;margin:0 auto;background:#16181c;border-radius:16px;padding:20px;color:#e7e9ea;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;';

          const header = document.createElement('div');
          header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #2f3336;';
          header.innerHTML = `<span style="font-size:18px;font-weight:700;">🧵 Thread (${threadTweets.length} tweets)</span>`;

          const closeBtn = document.createElement('button');
          closeBtn.textContent = '✕';
          closeBtn.style.cssText = 'background:none;border:none;color:#71767b;font-size:20px;cursor:pointer;padding:4px 8px;border-radius:50%;';
          closeBtn.addEventListener('click', () => overlay.remove());
          header.appendChild(closeBtn);
          container.appendChild(header);

          threadTweets.slice(0, opts.maxTweets).forEach((text, i) => {
            const p = document.createElement('div');
            p.style.cssText = 'padding:12px 0;border-bottom:1px solid #2f3336;font-size:15px;line-height:1.5;';
            p.innerHTML = `<span style="color:#71767b;font-size:12px;">${i + 1}/${threadTweets.length}</span><br>${text}`;
            container.appendChild(p);
          });

          // Copy all button
          const copyBtn = document.createElement('button');
          copyBtn.textContent = '📋 Copy Thread';
          copyBtn.style.cssText = 'display:block;margin:16px auto 0;background:#1d9bf0;color:white;border:none;padding:10px 24px;border-radius:20px;font-size:14px;font-weight:700;cursor:pointer;';
          copyBtn.addEventListener('click', () => {
            const fullText = threadTweets.map((t, i) => `${i + 1}. ${t}`).join('\n\n');
            navigator.clipboard.writeText(fullText);
            copyBtn.textContent = '✅ Copied!';
            setTimeout(() => { copyBtn.textContent = '📋 Copy Thread'; }, 2000);
          });
          container.appendChild(copyBtn);

          overlay.appendChild(container);
          overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
          document.body.appendChild(overlay);

          btn.innerHTML = '🧵';
          window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'threadReader', action: `🧵 Unrolled thread with ${threadTweets.length} tweets` }, '*');
          log(`🧵 Unrolled thread: ${threadTweets.length} tweets`, 'success');
        } else {
          btn.innerHTML = '🧵';
          log('⚠️ Could not collect thread tweets', 'warning');
        }
      });

      actionBar.appendChild(btn);
    }

    if (opts.showUnrollBtn) {
      document.querySelectorAll(SELECTORS.tweet).forEach(addUnrollButton);

      const observer = new MutationObserver(() => {
        document.querySelectorAll(SELECTORS.tweet).forEach(addUnrollButton);
      });
      observer.observe(document.body, { childList: true, subtree: true });
      window.__xactions_threadObserver = observer;

      while (!automationStopFlags['threadReader']) {
        await sleep(5000);
      }
      observer.disconnect();
      window.__xactions_threadObserver = null;
    }

    log('🧵 Thread Reader disabled', 'info');
    window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_COMPLETE', automationId: 'threadReader', summary: 'Thread reader stopped' }, '*');
  });

  // ============================================
  // QUICK STATS
  // ============================================
  registerAutomation('quickStats', async (settings) => {
    const { log, sleep, scrollBy, SELECTORS, storage } = window.XActions.Core;
    const opts = {
      showOverlay: settings.showOverlay !== false,
      trackDaily: settings.trackDaily !== false,
      sampleSize: settings.sampleSize || 20,
    };

    log('⚡ Quick Stats — calculating engagement rate...', 'info');

    // Collect engagement data from visible tweets
    const tweetStats = [];
    let scrollAttempts = 0;
    let noNewCount = 0;
    let lastCount = 0;
    const seenIds = new Set();

    while (tweetStats.length < opts.sampleSize && scrollAttempts < 40 && noNewCount < 3 && !automationStopFlags['quickStats']) {
      const tweets = document.querySelectorAll(SELECTORS.tweet);

      for (const tweet of tweets) {
        if (tweetStats.length >= opts.sampleSize) break;
        const link = tweet.querySelector(SELECTORS.tweetLink)?.href || '';
        const tweetId = link.match(/status\/(\d+)/)?.[1];
        if (!tweetId || seenIds.has(tweetId)) continue;
        seenIds.add(tweetId);

        const groups = tweet.querySelectorAll('[role="group"] button');
        let likes = 0, retweets = 0, replies = 0, views = 0;
        groups.forEach(btn => {
          const label = btn.getAttribute('aria-label') || '';
          const likeMatch = label.match(/(\d+)\s*[Ll]ike/);
          const rtMatch = label.match(/(\d+)\s*[Rr]e(?:post|tweet)/);
          const replyMatch = label.match(/(\d+)\s*repl/i);
          const viewMatch = label.match(/(\d+)\s*[Vv]iew/);
          if (likeMatch) likes = parseInt(likeMatch[1]);
          if (rtMatch) retweets = parseInt(rtMatch[1]);
          if (replyMatch) replies = parseInt(replyMatch[1]);
          if (viewMatch) views = parseInt(viewMatch[1]);
        });

        tweetStats.push({ tweetId, likes, retweets, replies, views, engagement: likes + retweets + replies });
      }

      if (tweetStats.length === lastCount) {
        noNewCount++;
      } else {
        noNewCount = 0;
        lastCount = tweetStats.length;
      }

      scrollBy(600);
      scrollAttempts++;
      await sleep(800);
    }

    if (tweetStats.length === 0) {
      log('⚠️ No tweets found to analyze', 'warning');
      window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_ERROR', automationId: 'quickStats', error: 'No tweets found' }, '*');
      return;
    }

    // Calculate stats
    const totalEngagement = tweetStats.reduce((sum, t) => sum + t.engagement, 0);
    const totalViews = tweetStats.reduce((sum, t) => sum + t.views, 0);
    const avgEngagement = totalEngagement / tweetStats.length;
    const engagementRate = totalViews > 0 ? ((totalEngagement / totalViews) * 100).toFixed(2) : 'N/A';
    const avgLikes = (tweetStats.reduce((s, t) => s + t.likes, 0) / tweetStats.length).toFixed(1);
    const avgRetweets = (tweetStats.reduce((s, t) => s + t.retweets, 0) / tweetStats.length).toFixed(1);
    const avgReplies = (tweetStats.reduce((s, t) => s + t.replies, 0) / tweetStats.length).toFixed(1);

    const results = {
      tweetCount: tweetStats.length,
      totalEngagement,
      totalViews,
      avgEngagement: avgEngagement.toFixed(1),
      engagementRate,
      avgLikes,
      avgRetweets,
      avgReplies,
      analyzedAt: Date.now(),
    };

    storage.set('quick_stats', results);

    // Track daily
    if (opts.trackDaily) {
      const dailyHistory = storage.get('daily_stats') || [];
      dailyHistory.unshift({ ...results, date: new Date().toISOString().slice(0, 10) });
      if (dailyHistory.length > 90) dailyHistory.length = 90;
      storage.set('daily_stats', dailyHistory);
    }

    // Show overlay on page
    if (opts.showOverlay) {
      // Remove existing overlay
      document.querySelector('#xactions-stats-overlay')?.remove();

      const overlay = document.createElement('div');
      overlay.id = 'xactions-stats-overlay';
      overlay.style.cssText = 'position:fixed;bottom:16px;right:16px;background:#16181c;border:1px solid #2f3336;border-radius:16px;padding:16px;z-index:99998;color:#e7e9ea;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;min-width:220px;box-shadow:0 4px 12px rgba(0,0,0,0.5);';
      overlay.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
          <span style="font-weight:700;font-size:14px;">⚡ Quick Stats</span>
          <button id="xactions-close-stats" style="background:none;border:none;color:#71767b;cursor:pointer;font-size:16px;">✕</button>
        </div>
        <div style="font-size:12px;color:#71767b;margin-bottom:8px;">${tweetStats.length} tweets analyzed</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
          <div style="background:#000;border-radius:8px;padding:8px;text-align:center;">
            <div style="font-size:18px;font-weight:700;color:#1d9bf0;">${engagementRate}%</div>
            <div style="font-size:10px;color:#71767b;">Eng. Rate</div>
          </div>
          <div style="background:#000;border-radius:8px;padding:8px;text-align:center;">
            <div style="font-size:18px;font-weight:700;color:#00ba7c;">${avgEngagement.toFixed(0)}</div>
            <div style="font-size:10px;color:#71767b;">Avg Eng.</div>
          </div>
          <div style="background:#000;border-radius:8px;padding:8px;text-align:center;">
            <div style="font-size:14px;font-weight:700;">❤️ ${avgLikes}</div>
            <div style="font-size:10px;color:#71767b;">Avg Likes</div>
          </div>
          <div style="background:#000;border-radius:8px;padding:8px;text-align:center;">
            <div style="font-size:14px;font-weight:700;">🔁 ${avgRetweets}</div>
            <div style="font-size:10px;color:#71767b;">Avg RTs</div>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      overlay.querySelector('#xactions-close-stats').addEventListener('click', () => overlay.remove());
    }

    window.postMessage({ source: 'xactions-page', type: 'ACTION_PERFORMED', automationId: 'quickStats', action: `⚡ Engagement rate: ${engagementRate}%, avg: ${avgEngagement.toFixed(1)} per tweet` }, '*');
    log(`⚡ Engagement rate: ${engagementRate}% | Avg: ${avgEngagement.toFixed(1)} | Likes: ${avgLikes} | RTs: ${avgRetweets} | Replies: ${avgReplies}`, 'success');
    window.postMessage({ source: 'xactions-page', type: 'AUTOMATION_COMPLETE', automationId: 'quickStats', summary: `${engagementRate}% engagement rate (${tweetStats.length} tweets)` }, '*');
  });

})();
