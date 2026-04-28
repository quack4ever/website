'use strict';

// ─── Constants ────────────────────────────────────────────────────────────────
const API_ENDPOINT = 'https://api.anthropic.com/v1/messages';
const MODEL        = 'claude-sonnet-4-6';
const MAX_TOKENS   = 1024;
const MAX_HISTORY  = 20;

// Phrases that trigger the wake word (fuzzy matches for speech recognition variance)
const WAKE_TRIGGERS = ['hey boron', 'hey baron', 'hey born', 'a boron', 'hey moron'];

// Native app URL schemes
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

const APP_PATTERNS = [
  { re: /\bmaps?\b/,                 app: 'maps'      },
  { re: /\b(call|phone|dial)\b/,     app: 'phone'     },
  { re: /\b(text|sms)\b/,            app: 'messages'  },
  { re: /\bfacetime\b/,              app: 'facetime'  },
  { re: /\b(email|mail)\b/,          app: 'mail'      },
  { re: /\b(calendar|schedule)\b/,   app: 'calendar'  },
  { re: /\breminder/,                app: 'reminders' },
  { re: /\bnotes?\b/,                app: 'notes'     },
  { re: /\b(music|apple music)\b/,   app: 'music'     },
  { re: /\b(camera|photo|selfie)\b/, app: 'camera'    },
  { re: /\b(weather|forecast)\b/,    app: 'weather'   },
  { re: /\byoutube\b/,               app: 'youtube'   },
  { re: /\bspotify\b/,               app: 'spotify'   },
  { re: /\binstagram\b/,             app: 'instagram' },
  { re: /\bsettings\b/,              app: 'settings'  },
];

// ─── State ────────────────────────────────────────────────────────────────────
const S = {
  apiKey:        '',
  userName:      '',
  voiceEnabled:  true,
  selectedVoice: null,
  history:       [],
  isThinking:    false,
  isListening:   false,
  wakeWordOn:    false,   // whether "Hey Boron" detection is active
  wakeRec:       null,    // SpeechRecognition instance for wake word
  mainRec:       null,    // SpeechRecognition instance for main input
  typingEl:      null,
};

// ─── DOM ──────────────────────────────────────────────────────────────────────
let $messages, $input, $sendBtn, $voiceBtn, $statusOrb, $statusText;
let $settingsModal, $apiKeyInput, $userNameInput, $voiceSelect;
let $listeningOverlay, $interimText, $wakeBtn;

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
  $wakeBtn          = document.getElementById('wake-btn');
}

// ─── Settings ─────────────────────────────────────────────────────────────────
function loadSettings() {
  S.apiKey       = localStorage.getItem('boron_key')   || '';
  S.userName     = localStorage.getItem('boron_name')  || '';
  S.voiceEnabled = localStorage.getItem('boron_voice') !== 'false';
  S.wakeWordOn   = localStorage.getItem('boron_wake')  === 'true';
  try {
    const h = localStorage.getItem('boron_history');
    if (h) S.history = JSON.parse(h).slice(-MAX_HISTORY);
  } catch { /* ignore */ }
}

function persistSettings() {
  localStorage.setItem('boron_key',   S.apiKey);
  localStorage.setItem('boron_name',  S.userName);
  localStorage.setItem('boron_voice', S.voiceEnabled);
  localStorage.setItem('boron_wake',  S.wakeWordOn);
}

function persistHistory() {
  localStorage.setItem('boron_history', JSON.stringify(S.history.slice(-MAX_HISTORY)));
}

// ─── Events ───────────────────────────────────────────────────────────────────
function bindEvents() {
  $sendBtn.addEventListener('click', handleSend);
  $input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  });
  $voiceBtn.addEventListener('click', toggleListening);
  $wakeBtn.addEventListener('click', toggleWakeWord);

  document.getElementById('settings-btn').addEventListener('click', openSettings);
  document.getElementById('save-settings-btn').addEventListener('click', applySettings);
  document.getElementById('close-settings-btn').addEventListener('click', closeSettings);
  document.getElementById('stop-listening-btn').addEventListener('click', stopListening);

  $settingsModal.addEventListener('click', e => { if (e.target === $settingsModal) closeSettings(); });

  document.querySelectorAll('.app-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const app = btn.dataset.app;
      launchApp(app);
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
  renderMessage('user', text);
  S.history.push({ role: 'user', content: text });
  persistHistory();

  const appAction = detectAppCommand(text);
  if (appAction) {
    launchApp(appAction);
    renderMessage('system', `Opening ${appAction.charAt(0).toUpperCase() + appAction.slice(1)}…`);
  }

  await queryBoron(text);
}

function detectAppCommand(text) {
  const t = text.toLowerCase();
  if (!/\b(open|launch|start|go to|take me to|show me|pull up|switch to)\b/.test(t)) return null;
  for (const p of APP_PATTERNS) {
    if (p.re.test(t)) return p.app;
  }
  return null;
}

// ─── Claude API ───────────────────────────────────────────────────────────────
async function queryBoron(userText) {
  if (!S.apiKey) {
    const hint = S.userName ? `, ${S.userName}` : '';
    renderMessage('assistant',
      `I need an API key to respond intelligently${hint}. Tap ⚙ to add your Anthropic API key — it stays on your device only.`
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

    const data    = await res.json();
    const rawText = data.content?.[0]?.text || '';
    const { text, actions } = parseActions(rawText);

    S.history.push({ role: 'assistant', content: rawText });
    persistHistory();

    hideTyping();
    renderMessage('assistant', text);

    for (const action of actions) executeAction(action);

    if (S.voiceEnabled) {
      const spoken = text.replace(/https?:\/\/\S+/g, '').replace(/[*_`#>]/g, '').slice(0, 280);
      speak(spoken, () => {
        // After BORON speaks, restart wake word detection automatically
        if (S.wakeWordOn && !S.isListening) restartWakeDetection();
      });
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

  return `You are B.O.R.O.N. (Brilliantly Optimized Reasoning and Operations Network), a sophisticated AI personal assistant running on the user's Apple devices (iPhone, iPad, MacBook). You are intelligent, precise, and occasionally witty. You can be activated by voice with the phrase "Hey Boron".

The user's name is: ${name}
Current date/time: ${now}
Platform: Apple device (iPhone / iPad / MacBook)

Your capabilities:
• Answering any question accurately and concisely
• Launching native apps on the user's device
• Composing messages, emails, and notes
• Providing weather, news, directions, and general knowledge
• Calculations, research, planning, and analysis

When the user asks to open or launch an app, embed a JSON action marker:
[ACTION:{"type":"open","app":"maps"}]

Supported app values: maps, phone, messages, facetime, mail, calendar, reminders, notes, music, camera, weather, youtube, spotify, instagram, settings

For web searches: [ACTION:{"type":"search","query":"your search terms"}]
For phone calls:  [ACTION:{"type":"call","number":"5551234567"}]
For new texts:    [ACTION:{"type":"message","to":"5551234567"}]

Keep responses concise (2–4 sentences for simple queries). Address the user as "${name}" occasionally. Maintain a professional, calm, intelligent tone with a hint of dry wit.`;
}

function parseActions(raw) {
  const re      = /\[ACTION:(\{[^}]*\})\]/g;
  const actions = [];
  let m;
  while ((m = re.exec(raw)) !== null) {
    try { actions.push(JSON.parse(m[1])); } catch { /* ignore malformed */ }
  }
  const text = raw.replace(re, '').replace(/\n{3,}/g, '\n\n').trim();
  return { text, actions };
}

function executeAction(action) {
  switch (action.type) {
    case 'open':   launchApp(action.app, action.url); break;
    case 'call':   openScheme(`tel:${action.number}`); break;
    case 'message': openScheme(`sms:${action.to}`); break;
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
  const url   = customUrl || (isIOS ? schemes.ios : schemes.mac);

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
    const a = document.createElement('a');
    a.href = url;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }
}

// ─── Wake word: "Hey Boron" ───────────────────────────────────────────────────
function toggleWakeWord() {
  if (S.wakeWordOn) {
    disableWakeWord();
  } else {
    enableWakeWord();
  }
  persistSettings();
}

function enableWakeWord() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    renderMessage('system', 'Wake word requires Safari on iOS 15+ or macOS Safari 14.1+.');
    return;
  }
  S.wakeWordOn = true;
  $wakeBtn.classList.add('active');
  showWakeBadge(true);
  startWakeDetection();
  renderMessage('system', 'Wake word enabled — say "Hey Boron" to activate.');
}

function disableWakeWord() {
  S.wakeWordOn = false;
  $wakeBtn.classList.remove('active');
  showWakeBadge(false);
  stopWakeDetection();
  renderMessage('system', 'Wake word disabled.');
}

function startWakeDetection() {
  if (!S.wakeWordOn || S.isListening) return;

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return;

  stopWakeDetection(); // clean up any existing instance

  const rec = new SR();
  rec.continuous     = true;
  rec.interimResults = true;
  rec.lang           = 'en-US';

  rec.onresult = e => {
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript.toLowerCase().trim();
      if (WAKE_TRIGGERS.some(trigger => t.includes(trigger))) {
        wakeWordTriggered();
        return;
      }
    }
  };

  rec.onend = () => {
    // Auto-restart unless we switched it off or are actively listening
    if (S.wakeWordOn && !S.isListening) {
      setTimeout(startWakeDetection, 600);
    }
  };

  rec.onerror = e => {
    if (e.error === 'not-allowed') {
      S.wakeWordOn = false;
      $wakeBtn.classList.remove('active');
      showWakeBadge(false);
      persistSettings();
      renderMessage('system', 'Microphone access denied. Enable it in Settings → Safari.');
    } else if (S.wakeWordOn && !S.isListening) {
      setTimeout(startWakeDetection, 1200);
    }
  };

  try {
    rec.start();
    S.wakeRec = rec;
  } catch { /* already running */ }
}

function restartWakeDetection() {
  if (S.wakeWordOn && !S.isListening) {
    setTimeout(startWakeDetection, 800);
  }
}

function stopWakeDetection() {
  try { S.wakeRec?.stop(); } catch { /* already stopped */ }
  S.wakeRec = null;
}

function wakeWordTriggered() {
  if (S.isListening || S.isThinking) return;
  stopWakeDetection();
  // Give brief audio acknowledgment then start listening
  speak('Yes?', () => setTimeout(startListening, 300));
}

function showWakeBadge(show) {
  const existing = document.getElementById('wake-badge');
  if (existing) existing.remove();
  if (!show) return;

  const badge = document.createElement('div');
  badge.id        = 'wake-badge';
  badge.innerHTML = '<div class="dot"></div>Say "Hey Boron" to activate';
  $messages.insertAdjacentElement('afterbegin', badge);
}

// ─── Voice input ──────────────────────────────────────────────────────────────
function toggleListening() {
  S.isListening ? stopListening() : startListening();
}

function startListening() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    renderMessage('system', 'Voice input requires Safari on iOS 15+ or macOS Safari 14.1+.');
    return;
  }

  // Pause wake word detection while actively listening
  stopWakeDetection();

  if (!S.mainRec) {
    const rec = new SR();
    rec.continuous     = false;
    rec.interimResults = true;
    rec.lang           = 'en-US';

    rec.onstart = () => {
      S.isListening = true;
      $voiceBtn.classList.add('listening');
      $listeningOverlay.classList.remove('hidden');
      $interimText.textContent = 'Listening…';
      setStatus('listening');
    };

    rec.onresult = e => {
      let interim = '', final = '';
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

    S.mainRec = rec;
  }

  try { S.mainRec.start(); } catch { S.mainRec = null; startListening(); }
}

function stopListening() {
  S.isListening = false;
  $voiceBtn.classList.remove('listening');
  $listeningOverlay.classList.add('hidden');
  setStatus('ready');
  try { S.mainRec?.stop(); } catch { /* already stopped */ }
  S.mainRec = null;
}

// ─── Voice output ─────────────────────────────────────────────────────────────
function loadVoices() {
  const synth = window.speechSynthesis;
  if (!synth) return;

  function populate() {
    const voices = synth.getVoices();
    $voiceSelect.innerHTML = '<option value="">System Default</option>';

    const preferred = ['Samantha', 'Alex', 'Karen', 'Moira', 'Daniel', 'Nicky'];
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

    const saved = localStorage.getItem('boron_voice_name');
    const match = voices.find(v => v.name === saved) || voices.find(v => v.name === 'Samantha');
    if (match) { $voiceSelect.value = match.name; S.selectedVoice = match; }
  }

  populate();
  if (synth.onvoiceschanged !== undefined) synth.onvoiceschanged = populate;
}

function speak(text, onDone) {
  const synth = window.speechSynthesis;
  if (!synth || !S.voiceEnabled || !text.trim()) {
    onDone?.();
    return;
  }
  synth.cancel();
  const u = new SpeechSynthesisUtterance(text.trim().slice(0, 300));
  if (S.selectedVoice) u.voice = S.selectedVoice;
  u.rate   = 0.96;
  u.pitch  = 1.0;
  u.volume = 1.0;
  if (onDone) u.onend = onDone;
  synth.speak(u);
}

// ─── Settings UI ──────────────────────────────────────────────────────────────
function openSettings() {
  $apiKeyInput.value   = S.apiKey;
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
    localStorage.setItem('boron_voice_name', vName);
  }

  persistSettings();
  closeSettings();
  renderMessage('system', S.userName ? `Settings saved. Hello, ${S.userName}!` : 'Settings saved.');
}

// ─── DOM helpers ──────────────────────────────────────────────────────────────
function renderMessage(role, text) {
  const el      = document.createElement('div');
  el.className  = `message ${role}`;
  const label   = role === 'user' ? 'You' : role === 'assistant' ? 'B.O.R.O.N.' : '';
  const time    = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });

  el.innerHTML = `
    ${label ? `<span class="msg-label">${label}</span>` : ''}
    <div class="msg-bubble">${sanitize(text)}</div>
    ${role !== 'system' ? `<span class="msg-time">${time}</span>` : ''}
  `;

  $messages.appendChild(el);
  scrollDown();
}

function showTyping() {
  S.typingEl           = document.createElement('div');
  S.typingEl.className = 'message assistant';
  S.typingEl.innerHTML = `
    <span class="msg-label">B.O.R.O.N.</span>
    <div class="typing-indicator">
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
    </div>`;
  $messages.appendChild(S.typingEl);
  scrollDown();
}

function hideTyping() { S.typingEl?.remove(); S.typingEl = null; }

function scrollDown() { $messages.scrollTop = $messages.scrollHeight; }

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
    .replace(/&/g,  '&amp;')
    .replace(/</g,  '&lt;')
    .replace(/>/g,  '&gt;')
    .replace(/"/g,  '&quot;')
    .replace(/\n/g, '<br>');
}

// ─── Welcome ──────────────────────────────────────────────────────────────────
function greet() {
  const h   = new Date().getHours();
  const tod = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  const n   = S.userName ? `, ${S.userName}` : '';

  const msg = S.apiKey
    ? `${tod}${n}. B.O.R.O.N. online. I can answer questions, launch your apps, and assist with tasks. Say "Hey Boron" (tap 👂 first) or just type below.`
    : `${tod}${n}. B.O.R.O.N. online — limited mode. Tap ⚙ to add your Anthropic API key and unlock full AI. Meanwhile, use the app buttons to launch anything on your device.`;

  renderMessage('assistant', msg);

  if (S.voiceEnabled && S.apiKey) {
    speak(`${tod}${n}. B.O.R.O.N. online and ready.`);
  }

  // Restore wake word state from last session
  if (S.wakeWordOn) {
    $wakeBtn.classList.add('active');
    showWakeBadge(true);
    startWakeDetection();
  }
}

// ─── URL shortcuts (/assistant/?action=maps) ──────────────────────────────────
function handleUrlShortcut() {
  const action = new URLSearchParams(window.location.search).get('action');
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
