'use strict';

// ─── Constants ────────────────────────────────────────────────────────────────
const API_ENDPOINT = 'https://api.anthropic.com/v1/messages';
const MODEL        = 'claude-sonnet-4-6';
const MAX_TOKENS   = 1024;
const MAX_HISTORY  = 20;

// Native app URL schemes for iOS / macOS
const APP_SCHEMES = {
  maps:      { ios: 'maps://',              mac: 'maps://' },
  phone:     { ios: 'tel:',                 mac: 'tel:' },
  messages:  { ios: 'sms:',                 mac: 'imessage://' },
  facetime:  { ios: 'facetime://',          mac: 'facetime://' },
  mail:      { ios: 'mailto:',              mac: 'mailto:' },
  calendar:  { ios: 'calshow://',           mac: 'ical://' },
  reminders: { ios: 'x-apple-reminder://', mac: 'x-apple-reminder://' },
  notes:     { ios: 'mobilenotes://',       mac: 'notes://' },
  music:     { ios: 'music://',             mac: 'music://' },
  camera:    { ios: 'camera://',            mac: null },
  weather:   { ios: 'https://weather.com',  mac: 'https://weather.com' },
  youtube:   { ios: 'youtube://',           mac: 'https://youtube.com' },
  spotify:   { ios: 'spotify://',           mac: 'spotify://' },
  instagram: { ios: 'instagram://',         mac: 'https://instagram.com' },
  settings:  { ios: 'app-settings://',      mac: null },
  appstore:  { ios: 'itms-apps://',         mac: 'macappstore://' },
  photos:    { ios: 'photos-redirect://',   mac: null },
};

// Voice commands that trigger app launches (require an "open/launch" verb)
const APP_PATTERNS = [
  { re: /\bmaps?\b/,                   app: 'maps'      },
  { re: /\b(call|phone|dial)\b/,       app: 'phone'     },
  { re: /\b(text|sms)\b/,              app: 'messages'  },
  { re: /\bfacetime\b/,                app: 'facetime'  },
  { re: /\b(email|mail)\b/,            app: 'mail'      },
  { re: /\b(calendar|schedule)\b/,     app: 'calendar'  },
  { re: /\breminder/,                  app: 'reminders' },
  { re: /\bnotes?\b/,                  app: 'notes'     },
  { re: /\b(music|apple music)\b/,     app: 'music'     },
  { re: /\b(camera|photo|selfie)\b/,   app: 'camera'    },
  { re: /\b(weather|forecast)\b/,      app: 'weather'   },
  { re: /\byoutube\b/,                 app: 'youtube'   },
  { re: /\bspotify\b/,                 app: 'spotify'   },
  { re: /\binstagram\b/,               app: 'instagram' },
  { re: /\bsettings\b/,                app: 'settings'  },
];

// ─── State ────────────────────────────────────────────────────────────────────
const S = {
  apiKey:        '',
  userName:      '',
  voiceEnabled:  true,
  selectedVoice: null,
  history:       [],    // Claude API message history
  isThinking:    false,
  isListening:   false,
  recognition:   null,
  typingEl:      null,
};

// ─── DOM refs ─────────────────────────────────────────────────────────────────
let $messages, $input, $sendBtn, $voiceBtn, $statusOrb, $statusText;
let $settingsModal, $apiKeyInput, $userNameInput, $voiceSelect;
let $listeningOverlay, $interimText;

// ─── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  cacheDom();
  loadSettings();
  bindEvents();
  loadVoices();
  registerSW();
  handleUrlShortcut();
  greet();
});

function cacheDom() {
  $messages         = document.getElementById('messages');
  $input            = document.getElementById('text-input');
  $sendBtn          = document.getElementById('send-btn');
  $voiceBtn         = document.getElementById('voice-btn');
  $statusOrb        = document.getElementById('status-orb');
  $statusText       = document.getElementById('status-text');
  $settingsModal    = document.getElementById('settings-modal');
  $apiKeyInput      = document.getElementById('api-key-input');
  $userNameInput    = document.getElementById('user-name-input');
  $voiceSelect      = document.getElementById('voice-select');
  $listeningOverlay = document.getElementById('listening-overlay');
  $interimText      = document.getElementById('interim-text');
}

// ─── Settings persistence ─────────────────────────────────────────────────────
function loadSettings() {
  S.apiKey       = localStorage.getItem('jarvis_key')   || '';
  S.userName     = localStorage.getItem('jarvis_name')  || '';
  S.voiceEnabled = localStorage.getItem('jarvis_voice') !== 'false';
  try {
    const h = localStorage.getItem('jarvis_history');
    if (h) S.history = JSON.parse(h).slice(-MAX_HISTORY);
  } catch { /* ignore */ }
}

function persistSettings() {
  localStorage.setItem('jarvis_key',   S.apiKey);
  localStorage.setItem('jarvis_name',  S.userName);
  localStorage.setItem('jarvis_voice', S.voiceEnabled);
}

function persistHistory() {
  localStorage.setItem('jarvis_history', JSON.stringify(S.history.slice(-MAX_HISTORY)));
}

// ─── Events ───────────────────────────────────────────────────────────────────
function bindEvents() {
  $sendBtn.addEventListener('click', handleSend);
  $input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  });
  $voiceBtn.addEventListener('click', toggleListening);

  document.getElementById('settings-btn').addEventListener('click', openSettings);
  document.getElementById('save-settings-btn').addEventListener('click', applySettings);
  document.getElementById('close-settings-btn').addEventListener('click', closeSettings);
  document.getElementById('stop-listening-btn').addEventListener('click', stopListening);

  $settingsModal.addEventListener('click', e => { if (e.target === $settingsModal) closeSettings(); });

  document.querySelectorAll('.app-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const app = btn.dataset.app;
      launchApp(app);
      // Acknowledge in chat
      renderMessage('system', `Opening ${app.charAt(0).toUpperCase() + app.slice(1)}…`);
    });
  });
}

function handleSend() {
  const text = $input.value.trim();
  if (!text || S.isThinking) return;
  $input.value = '';
  handleUserInput(text);
}

// ─── Core loop ────────────────────────────────────────────────────────────────
async function handleUserInput(text) {
  // Render user bubble and add to history
  renderMessage('user', text);
  S.history.push({ role: 'user', content: text });
  persistHistory();

  // Check if this is a quick app-launch command
  const appAction = detectAppCommand(text);
  if (appAction) {
    launchApp(appAction);
    renderMessage('system', `Opening ${appAction.charAt(0).toUpperCase() + appAction.slice(1)}…`);
  }

  // Always consult Claude for a natural response
  await queryJarvis(text);
}

// Return app name string if the user is asking to open an app, else null
function detectAppCommand(text) {
  const t = text.toLowerCase();
  const hasLaunchVerb = /\b(open|launch|start|go to|take me to|show me|pull up|switch to)\b/.test(t);
  if (!hasLaunchVerb) return null;
  for (const p of APP_PATTERNS) {
    if (p.re.test(t)) return p.app;
  }
  return null;
}

// ─── Claude API ───────────────────────────────────────────────────────────────
async function queryJarvis(userText) {
  if (!S.apiKey) {
    const hint = S.userName ? `, ${S.userName}` : '';
    renderMessage('assistant',
      `I need an API key to respond intelligently${hint}. Tap ⚙ to add your Anthropic API key — it stays on your device and is never shared.`
    );
    speak('Please configure your Anthropic API key in settings.');
    return;
  }

  showTyping();
  setStatus('thinking');
  S.isThinking = true;

  try {
    const res = await fetch(API_ENDPOINT, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': S.apiKey,
        'anthropic-version': '2023-06-01',
        'anthropic-dangerous-direct-browser-access': 'true',
      },
      body: JSON.stringify({
        model:      MODEL,
        max_tokens: MAX_TOKENS,
        system:     buildSystemPrompt(),
        messages:   S.history.slice(-MAX_HISTORY),
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error?.message || `API error ${res.status}`);
    }

    const data     = await res.json();
    const rawText  = data.content?.[0]?.text || '';
    const { text, actions } = parseActions(rawText);

    // Add assistant reply to history using raw text (so future context is complete)
    S.history.push({ role: 'assistant', content: rawText });
    persistHistory();

    hideTyping();
    renderMessage('assistant', text);

    // Execute any [ACTION:...] markers Claude embedded
    for (const action of actions) executeAction(action);

    // Speak a concise version
    if (S.voiceEnabled) {
      const spoken = text.replace(/https?:\/\/\S+/g, '').replace(/[*_`#>]/g, '').slice(0, 280);
      speak(spoken);
    }

    setStatus('ready');
  } catch (err) {
    hideTyping();
    renderMessage('system', `Error: ${err.message}`);
    setStatus('error');
    setTimeout(() => setStatus('ready'), 3500);
  } finally {
    S.isThinking = false;
  }
}

function buildSystemPrompt() {
  const name = S.userName || 'there';
  const now  = new Date().toLocaleString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long',
    day: 'numeric', hour: '2-digit', minute: '2-digit',
  });

  return `You are J.A.R.V.I.S. (Just A Rather Very Intelligent System), a sophisticated AI personal assistant running on the user's Apple devices (iPhone, iPad, MacBook). You are intelligent, precise, and occasionally witty — modeled after the AI from Iron Man.

The user's name is: ${name}
Current date/time: ${now}
Platform: Apple device (iPhone / iPad / MacBook)

Your capabilities:
• Answering any question accurately and concisely
• Launching native apps on the user's device
• Composing messages, emails, and notes
• Providing weather, news, directions, and general knowledge
• Calculations, research, planning, and analysis

When the user asks to open or launch an app, embed a JSON action marker in your response:
[ACTION:{"type":"open","app":"maps"}]

Supported app values: maps, phone, messages, facetime, mail, calendar, reminders, notes, music, camera, weather, youtube, spotify, instagram, settings

For web searches: [ACTION:{"type":"search","query":"your search terms"}]
For phone calls:  [ACTION:{"type":"call","number":"5551234567"}]
For new texts:    [ACTION:{"type":"message","to":"5551234567"}]

Keep responses concise (2–4 sentences for simple queries). Address the user as "${name}" occasionally. Maintain the JARVIS personality: professional, calm, intelligent, with a hint of dry wit.`;
}

// Parse [ACTION:{...}] markers out of a response, return clean text + action list
function parseActions(raw) {
  const actionRe = /\[ACTION:(\{[^}]*\})\]/g;
  const actions  = [];
  let m;
  while ((m = actionRe.exec(raw)) !== null) {
    try { actions.push(JSON.parse(m[1])); } catch { /* ignore malformed */ }
  }
  const text = raw.replace(actionRe, '').replace(/\n{3,}/g, '\n\n').trim();
  return { text, actions };
}

function executeAction(action) {
  switch (action.type) {
    case 'open':
      launchApp(action.app, action.url);
      break;
    case 'call':
      openScheme(`tel:${action.number}`);
      break;
    case 'message':
      openScheme(`sms:${action.to}`);
      break;
    case 'search':
      window.open(`https://www.google.com/search?q=${encodeURIComponent(action.query)}`, '_blank');
      break;
  }
}

// ─── App launching ────────────────────────────────────────────────────────────
function launchApp(appName, customUrl) {
  const schemes = APP_SCHEMES[appName];
  if (!schemes && !customUrl) return;

  const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent);
  let url = customUrl || (isIOS ? schemes.ios : schemes.mac);

  if (!url) {
    renderMessage('system', `This app isn't available on your current device.`);
    return;
  }

  openScheme(url);
}

function openScheme(url) {
  if (!url) return;
  if (url.startsWith('http')) {
    window.open(url, '_blank', 'noopener');
  } else {
    // Use anchor click for URL scheme on iOS — avoids popup blockers
    const a = document.createElement('a');
    a.href = url;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }
}

// ─── Voice input ──────────────────────────────────────────────────────────────
function setupRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return null;

  const rec = new SR();
  rec.continuous      = false;
  rec.interimResults  = true;
  rec.lang            = 'en-US';

  rec.onstart = () => {
    S.isListening = true;
    $voiceBtn.classList.add('listening');
    $listeningOverlay.classList.remove('hidden');
    $interimText.textContent = 'Listening…';
    setStatus('listening');
  };

  rec.onresult = e => {
    let interim = '';
    let final   = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      if (e.results[i].isFinal) final   += e.results[i][0].transcript;
      else                       interim += e.results[i][0].transcript;
    }
    $interimText.textContent = final || interim || 'Listening…';
    if (final.trim()) {
      stopListening();
      handleUserInput(final.trim());
    }
  };

  rec.onerror = e => {
    if (e.error !== 'aborted') {
      renderMessage('system', `Microphone error: ${e.error}. Check permissions in Settings → Safari.`);
    }
    stopListening();
  };

  rec.onend = () => { if (S.isListening) stopListening(); };

  return rec;
}

function toggleListening() {
  S.isListening ? stopListening() : startListening();
}

function startListening() {
  if (!S.recognition) S.recognition = setupRecognition();
  if (!S.recognition) {
    renderMessage('system', 'Voice input requires Safari on iOS 15+ or macOS Safari 14.1+.');
    return;
  }
  try { S.recognition.start(); } catch { /* already started */ }
}

function stopListening() {
  S.isListening = false;
  $voiceBtn.classList.remove('listening');
  $listeningOverlay.classList.add('hidden');
  setStatus('ready');
  try { S.recognition?.stop(); } catch { /* already stopped */ }
}

// ─── Voice output ─────────────────────────────────────────────────────────────
function loadVoices() {
  const synth = window.speechSynthesis;
  if (!synth) return;

  function populate() {
    const voices = synth.getVoices();
    $voiceSelect.innerHTML = '<option value="">System Default</option>';

    // Preferred Apple/English voices first
    const preferred = ['Samantha', 'Alex', 'Karen', 'Moira', 'Daniel', 'Nicky', 'Siri'];
    const sorted = [...voices].sort((a, b) => {
      const ai = preferred.findIndex(n => a.name.includes(n));
      const bi = preferred.findIndex(n => b.name.includes(n));
      return (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi);
    });

    sorted.forEach(v => {
      const opt = document.createElement('option');
      opt.value       = v.name;
      opt.textContent = `${v.name} (${v.lang})`;
      $voiceSelect.appendChild(opt);
    });

    const saved = localStorage.getItem('jarvis_voice_name');
    const match = voices.find(v => v.name === saved) || voices.find(v => v.name === 'Samantha');
    if (match) {
      $voiceSelect.value = match.name;
      S.selectedVoice    = match;
    }
  }

  populate();
  if (synth.onvoiceschanged !== undefined) synth.onvoiceschanged = populate;
}

function speak(text) {
  const synth = window.speechSynthesis;
  if (!synth || !S.voiceEnabled || !text.trim()) return;
  synth.cancel();
  const u = new SpeechSynthesisUtterance(text.trim().slice(0, 300));
  if (S.selectedVoice) u.voice = S.selectedVoice;
  u.rate   = 0.96;
  u.pitch  = 1.0;
  u.volume = 1.0;
  synth.speak(u);
}

// ─── Settings UI ──────────────────────────────────────────────────────────────
function openSettings() {
  $apiKeyInput.value  = S.apiKey;
  $userNameInput.value = S.userName;
  $settingsModal.classList.remove('hidden');
}

function closeSettings() {
  $settingsModal.classList.add('hidden');
}

function applySettings() {
  S.apiKey   = $apiKeyInput.value.trim();
  S.userName = $userNameInput.value.trim();

  const vName = $voiceSelect.value;
  if (vName) {
    S.selectedVoice = window.speechSynthesis?.getVoices().find(v => v.name === vName) || null;
    localStorage.setItem('jarvis_voice_name', vName);
  }

  persistSettings();
  closeSettings();
  renderMessage('system', S.userName ? `Settings saved. Hello, ${S.userName}!` : 'Settings saved.');
}

// ─── DOM helpers ──────────────────────────────────────────────────────────────
function renderMessage(role, text) {
  const el    = document.createElement('div');
  el.className = `message ${role}`;

  const label = role === 'user' ? 'You' : role === 'assistant' ? 'J.A.R.V.I.S' : '';
  const time  = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });

  el.innerHTML = `
    ${label ? `<span class="msg-label">${label}</span>` : ''}
    <div class="msg-bubble">${sanitize(text)}</div>
    ${role !== 'system' ? `<span class="msg-time">${time}</span>` : ''}
  `;

  $messages.appendChild(el);
  scrollDown();
}

function showTyping() {
  S.typingEl = document.createElement('div');
  S.typingEl.className = 'message assistant';
  S.typingEl.innerHTML = `
    <span class="msg-label">J.A.R.V.I.S</span>
    <div class="typing-indicator">
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
    </div>`;
  $messages.appendChild(S.typingEl);
  scrollDown();
}

function hideTyping() {
  S.typingEl?.remove();
  S.typingEl = null;
}

function scrollDown() {
  $messages.scrollTop = $messages.scrollHeight;
}

function setStatus(status) {
  const map = {
    ready:     { text: 'Ready',      cls: ''          },
    thinking:  { text: 'Processing', cls: 'thinking'  },
    listening: { text: 'Listening',  cls: 'listening' },
    error:     { text: 'Error',      cls: 'error'     },
  };
  const s = map[status] || map.ready;
  $statusText.textContent = s.text;
  $statusOrb.className    = `status-orb ${s.cls}`;
}

function sanitize(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/\n/g, '<br>');
}

// ─── Welcome message ──────────────────────────────────────────────────────────
function greet() {
  const h     = new Date().getHours();
  const tod   = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  const name  = S.userName ? `, ${S.userName}` : '';

  const msg = S.apiKey
    ? `${tod}${name}. J.A.R.V.I.S. online. I can answer questions, launch your apps, set reminders, help compose messages, and more. How can I assist you?`
    : `${tod}${name}. J.A.R.V.I.S. online — limited mode. Tap ⚙ to add your Anthropic API key and unlock full AI capabilities. In the meantime, use the app buttons below to launch any app on your device.`;

  renderMessage('assistant', msg);

  if (S.voiceEnabled && S.apiKey) {
    speak(`${tod}${name}. J.A.R.V.I.S. online and ready.`);
  }
}

// ─── PWA shortcuts (/assistant/?action=maps) ──────────────────────────────────
function handleUrlShortcut() {
  const params = new URLSearchParams(window.location.search);
  const action = params.get('action');
  if (action && APP_SCHEMES[action]) {
    launchApp(action);
    renderMessage('system', `Opening ${action.charAt(0).toUpperCase() + action.slice(1)}…`);
  }
}

// ─── Service worker ───────────────────────────────────────────────────────────
function registerSW() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/assistant/sw.js').catch(() => { /* no-op in dev */ });
  }
}
