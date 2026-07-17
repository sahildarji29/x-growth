// XActions Extension — Content Script Bridge
// Injected into x.com/twitter.com pages
// Bridges popup ↔ page context via chrome.runtime messaging
// by nichxbt

(() => {
  // Prevent double-injection
  if (window.__xactions_bridge_loaded) return;
  window.__xactions_bridge_loaded = true;

  // ============================================
  // INJECT AUTOMATION CODE INTO PAGE CONTEXT
  // ============================================
  function injectScript() {
    const script = document.createElement('script');
    script.src = chrome.runtime.getURL('content/injected.js');
    script.onload = () => script.remove();
    (document.head || document.documentElement).appendChild(script);
  }

  // Wait for DOM ready then inject
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectScript);
  } else {
    injectScript();
  }

  // ============================================
  // PAGE ↔ EXTENSION MESSAGING
  // ============================================
  
  // Listen for messages from injected page script
  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    if (!event.data || event.data.source !== 'xactions-page') return;

    const msg = event.data;

    switch (msg.type) {
      case 'ACTION_PERFORMED':
        chrome.runtime.sendMessage({
          type: 'ACTION_PERFORMED',
          automationId: msg.automationId,
          action: msg.action,
        });
        chrome.runtime.sendMessage({
          type: 'ACTIVITY_LOG',
          entry: {
            time: Date.now(),
            type: 'action',
            automation: msg.automationId,
            message: msg.action,
          },
        });
        break;

      case 'AUTOMATION_COMPLETE':
        chrome.runtime.sendMessage({
          type: 'ACTIVITY_LOG',
          entry: {
            time: Date.now(),
            type: 'complete',
            automation: msg.automationId,
            message: `${msg.automationId} completed — ${msg.summary || 'done'}`,
          },
        });
        break;

      case 'AUTOMATION_ERROR':
        chrome.runtime.sendMessage({
          type: 'ACTIVITY_LOG',
          entry: {
            time: Date.now(),
            type: 'error',
            automation: msg.automationId,
            message: msg.error,
          },
        });
        break;

      case 'ACCOUNT_INFO':
        chrome.runtime.sendMessage({
          type: 'ACCOUNT_INFO_RESPONSE',
          data: msg.data,
        });
        break;

      case 'GENERATE_COMMENT':
        chrome.runtime.sendMessage({
          type: 'GENERATE_COMMENT',
          tweetText: msg.tweetText,
          userProfile: msg.userProfile,
          requestId: msg.requestId,
        });
        break;

      case 'GENERATE_COMMENT_GROQ':
        chrome.runtime.sendMessage({
          type: 'GENERATE_COMMENT_GROQ',
          tweetText: msg.tweetText,
          tweetAuthor: msg.tweetAuthor,
          apiKey: msg.apiKey,
          model: msg.model,
          requestId: msg.requestId,
        });
        break;
    }
  });

  // Listen for messages from popup/background
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    switch (message.type) {
      case 'RUN_AUTOMATION':
        window.postMessage({
          source: 'xactions-extension',
          type: 'RUN_AUTOMATION',
          automationId: message.automationId,
          settings: message.settings,
        }, '*');
        sendResponse({ success: true });
        break;

      case 'STOP_AUTOMATION':
        window.postMessage({
          source: 'xactions-extension',
          type: 'STOP_AUTOMATION',
          automationId: message.automationId,
        }, '*');
        sendResponse({ success: true });
        break;

      case 'STOP_ALL':
        window.postMessage({
          source: 'xactions-extension',
          type: 'STOP_ALL',
        }, '*');
        sendResponse({ success: true });
        break;

      case 'PAUSE_ALL':
        window.postMessage({
          source: 'xactions-extension',
          type: 'PAUSE_ALL',
        }, '*');
        sendResponse({ success: true });
        break;

      case 'RESUME_ALL':
        window.postMessage({
          source: 'xactions-extension',
          type: 'RESUME_ALL',
        }, '*');
        sendResponse({ success: true });
        break;

      case 'GET_ACCOUNT_INFO':
        window.postMessage({
          source: 'xactions-extension',
          type: 'GET_ACCOUNT_INFO',
        }, '*');
        sendResponse({ success: true });
        break;

      case 'COMMENT_GENERATED':
        window.postMessage({
          source: 'xactions-extension',
          type: 'COMMENT_GENERATED',
          comment: message.comment,
          requestId: message.requestId,
        }, '*');
        sendResponse({ success: true });
        break;

      case 'PING':
        sendResponse({ pong: true });
        break;

      default:
        sendResponse({ error: 'Unknown message type' });
    }
    return true;
  });

  console.log('🔌 XActions bridge loaded');
})();
