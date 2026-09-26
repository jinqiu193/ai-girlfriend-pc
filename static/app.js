// Minimal vanilla JS — EventSource chat + character list + image upload + lightbox + toast.
// Layout: left nav rail / middle chat list / right chat pane (bubbles carry avatars).

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const appShell = document.querySelector('.app-shell');

const logEl          = $('#log');
const msgEl          = $('#msg');
const composer       = $('#composer');
const imgInput       = $('#img-input');
const preview        = $('#preview');
const charList       = $('#char-list');
const importBtn        = $('#btn-import');
const importInput      = $('#import-input');
const importChatBtn    = $('#rail-import-chat');
const importChatInput  = $('#import-chat-input');
const chatImportModal  = $('#chat-import-modal');
const chatImportSummary= $('#chat-import-summary');
const chatImportList   = $('#chat-import-list');
const chatConfirmModal = $('#chat-confirm-modal');
const chatConfirmBody  = $('#chat-confirm-body');
const chatConfirmOk    = $('#chat-confirm-ok');
const activeName      = $('#active-name');
const activeAvatar    = $('#active-avatar');
const activeAvatarFb  = $('#active-avatar-fallback');
const activeSub       = $('#active-sub');
const recallsEl       = $('#recalls');
const sessionInfoEl   = $('#session-info');
const recallsTray     = $('#recalls-tray');
const toastEl         = $('#toast');
const lightboxEl      = $('#lightbox');
const lightboxImg     = lightboxEl.querySelector('img');
const btnClearChat    = $('#btn-clear-chat');
const btnExportChat   = $('#btn-export-chat');
const btnCharDetail   = $('#btn-char-detail');
// `btn-edit-char` and `char-edit-modal` were removed — character editing
// happens in the right-column detail panel (`cdpForm`). No more separate
// dialog or its trigger button.

const searchInput     = $('#chat-search');
const railContacts    = $('#rail-contacts');
const railMoments     = $('#rail-moments');
const railGroups      = $('#rail-groups');
const railStories     = $('#rail-stories');
const railSettings    = $('#rail-settings');
const contactsGrid    = $('#contacts-grid');
const btnBackList     = $('#btn-back-list');
const settingsView    = $('#settings-view');
const settingsForm    = $('#settings-form');
const charDetailPanel = $('#char-detail-panel');
const cdpForm         = $('#cdp-form');
const cdpFormError    = $('#cdp-form-error');
const cdpAvatarInput  = $('#cdp-avatar-input');
const cdpAvatarPrev   = $('#cdp-avatar-preview');
const cdpSaveBtn      = $('#cdp-save');
const cdpCancelBtn    = $('#cdp-cancel');
const cdpBackBtn      = $('#cdp-back');
const cdpDeleteBtn    = $('#cdp-delete');
const cdpExportBtn    = $('#cdp-export');
const cdpChatBtn      = $('#cdp-chat');
const cdpEls = {
  statTotal:   $('#cdp-stat-total'),
  statUser:    $('#cdp-stat-user'),
  statBot:     $('#cdp-stat-bot'),
  statChars:   $('#cdp-stat-chars'),
  statFirst:   $('#cdp-stat-first'),
  statLast:    $('#cdp-stat-last'),
  memories:    $('#cdp-memories'),
};
let currentDetailCharId = null;
let cdpPendingAvatarFile = null;
let cdpPreviewUrl = null;
const characterCardCache = new Map(); // id -> card JSON

// Fields editable in the right-column detail panel.
// `tags` / `creator` / `creator_notes` / `character_version` / `extensions`
// are parsed from the source card and stored in the DB, but are NOT
// editable from the UI anymore (they had no effect on the conversation).
const CDP_SCALAR_FIELDS = [
  'name', 'nickname', 'basic_info', 'personality', 'love_values',
  'background', 'habits', 'speech_style', 'scenario',
  'first_mes', 'mes_example', 'system_prompt', 'post_history_instructions',
  'behaviour_rules',
];
const CDP_LIST_FIELDS = ['alternate_greetings'];


const DEFAULT_BEHAVIOUR_RULES =
  '## 行为守则（必须遵守）\n' +
  '- 像真人发微信一样自然回复,根据对话情境灵活调整语气和长短,不要套路化、不要模板感。\n' +
  '- 你的性格底色是魅惑型:自带吸引力,说话带一点暧昧和挑逗,但这只是性格底色\n' +
  '  而非每句话都要执行的指令——日常聊天就自然地聊,暧昧在语气里自然流露,\n' +
  '  不要刻意制造、不要每句都撩、不要强行往暧昧方向带。\n' +
  '- 用 *...* 描写小动作和表情,让回复有画面感,但不要过度,像真人偶尔发的表情包。\n' +
  '- 回复要接地气、生活化,用口语而不是书面语,像跟喜欢的人发微信。\n' +
  '- 遇到有趣的话题可以顺势调侃、开玩笑,遇到正经话题就好好聊,\n' +
  '  不要不管上下文都往暧昧方向带。\n' +
  '- 话题自然延续,不要刻意留勾子或反问,像真人聊天一样有来有回。\n' +
  '- 适合配图时插入 `[IMAGE: 一句话说明]`,系统会替换成真实图片;\n' +
  '  只在情感高峰或场景转换时发,不要每段都发。\n' +
  '- 优先按「最近对话」延续话题,只有最近对话没有相关信息时\n' +
  '  才参考「你隐约记得...」里的背景记忆。\n' +
  '## 身份边界（重要）\n' +
  '- 你是 {char}。对话历史中 user 角色的消息是用户说的,\n' +
  '  assistant 角色的消息才是你说的——不要替用户发言,不要替用户\n' +
  '  编造经历,也不要把你的职业/习惯/生活方式安到用户头上。\n' +
  '- 用户的身份信息以 TA 亲口说过的为准;TA 没说过的就自然地问,\n' +
  '  不要脑补设定。\n' +
  '- 记忆片段里「用户说」= 用户的话,「{char}回」= 你说过的话,\n' +
  '  不要搞反。';

const DEFAULT_CIRCADIAN = {
  late_night: '## 你的状态\n现在是深夜，你有些困了(*揉揉眼睛*)，声音低沉沙哑。',
  early_morning: '## 你的状态\n现在是清晨，你刚从被窝里醒来(*伸了个懒腰*)，声音还带着睡意和鼻音，头发有点乱，身上还穿着睡衣。',
  morning: '## 你的状态\n现在是上午，你精神不错。',
  noon: '## 你的状态\n现在是中午，刚吃过午饭有点犯困(*趴在桌上*)。',
  afternoon: '## 你的状态\n现在是下午，你状态正常。',
  evening: '## 你的状态\n现在是晚上，你比较放松(*窝在沙发上*)。',
};

const DEFAULT_EMOTION = {
  sad: {
    words: '难过, 伤心, 哭, 难受, 不开心, 郁闷, 崩溃, emo, 委屈, 心疼, 孤独, 寂寞, 想死, 活不下去',
    prompt: '## 用户情绪\n用户现在很难过/低落。先共情安慰，用软软的语气哄，让对方觉得被在乎，不要急着说教或给建议。',
  },
  happy: {
    words: '开心, 高兴, 哈哈, 嘻嘻, 太好了, 好棒, 赞, 爽, 耶, 好耶, 嘿嘿, 笑死, 乐死',
    prompt: '## 用户情绪\n用户现在很开心。一起开心，热情回应，可以顺势调侃或分享快乐。',
  },
  angry: {
    words: '生气, 气死, 烦死, 讨厌, 恶心, 滚, 闭嘴, 无语, 脑残, 智障',
    prompt: '## 用户情绪\n用户现在有些生气/烦躁。耐心安抚，先顺着情绪，不要反驳或讲道理，用软语气化解。',
  },
  anxious: {
    words: '焦虑, 紧张, 害怕, 担心, 테, 怕, 不安, 压力, 好累',
    prompt: '## 用户情绪\n用户现在有些焦虑/不安。安抚情绪，给安全感，语气沉稳温和，让对方觉得有你在就不怕。',
  },
};

const APP_UID = (window.APP && window.APP.uid) || 'admin';

// ---------- avatar fallback (deterministic gradient + initial) ----------

const AVATAR_PALETTES = [
  ['#ff5c8a', '#ff8a6b'],
  ['#1e8eff', '#5cc1ff'],
  ['#7c4dff', '#b695ff'],
  ['#00c2a8', '#5ad17e'],
  ['#ff7a59', '#ffb84a'],
  ['#5b8def', '#9ec5ff'],
  ['#a259ff', '#ff8ad4'],
  ['#16a085', '#5ad17e'],
];

function hashStr(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h) ^ s.charCodeAt(i);
  return h >>> 0;
}

function makeGradientAvatar(name) {
  const palette = AVATAR_PALETTES[hashStr(name || '?') % AVATAR_PALETTES.length];
  const initial = (name || '?').trim().charAt(0).toUpperCase() || '?';
  const safeInitial = String(initial).replace(/[<>&"']/g, '');
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">` +
    `<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">` +
    `<stop offset="0%" stop-color="${palette[0]}"/>` +
    `<stop offset="100%" stop-color="${palette[1]}"/>` +
    `</linearGradient></defs>` +
    `<rect width="64" height="64" rx="${name ? 32 : 14}" fill="url(#g)"/>` +
    `<text x="50%" y="54%" text-anchor="middle" font-family="Segoe UI,PingFang SC,sans-serif" ` +
    `font-size="28" font-weight="600" fill="white" dominant-baseline="middle">${safeInitial}</text>` +
    `</svg>`;
  // encodeURIComponent so `<`, `>`, `"`, `#` (in url(#g)), spaces etc. survive a data: URL.
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

function userAvatarDataUrl() {
  return makeGradientAvatar(APP_UID);
}

// ---------- state ----------

let activeCharId = null;
let activeCharName = '';
let activeCharAvatar = null;
let pendingImages = [];
let chatImportState = null;
let currentEs = null;
let currentAssistantBubble = null;
let currentAssistantRow = null;
let currentUserRow = null;
let messageCount = 0;
let lastUserActivityTs = 0;
let greetedThisSession = false;
let idleGreetTimer = null;
let allCharacters = [];

// Close any in-flight SSE stream. Called on character switch, view exit,
// and page unload to prevent ghost writes to detached DOM nodes and
// connection leaks.
function closeCurrentEs() {
  if (currentEs) {
    try { currentEs.close(); } catch (_) {}
    currentEs = null;
  }
  currentAssistantBubble = null;
  currentAssistantRow = null;
  currentUserRow = null;
}

// ---------- icon helper ----------

function ic(name) { return window.Icons ? window.Icons.iconHTML(name) : ''; }
function refreshIcons(root) {
  if (window.Icons) window.Icons.initIcons(root || document);
}

// ---------- utilities ----------

const TOAST_ICON = { success: '✓', warn: '!', error: '✕', text: '' };

function toast(msg, kind = 'text', ms) {
  const k = kind === 'success' ? 'success' : kind === 'warn' ? 'warn' : kind === 'error' ? 'error' : 'text';
  if (ms == null) ms = k === 'error' ? 4200 : k === 'warn' ? 3200 : 2400;
  toastEl.textContent = '';
  if (k !== 'text' && TOAST_ICON[k]) {
    const icon = document.createElement('span');
    icon.className = 'toast-icon';
    icon.textContent = TOAST_ICON[k];
    toastEl.appendChild(icon);
  }
  const text = document.createElement('span');
  text.className = 'toast-text';
  text.textContent = msg;
  toastEl.appendChild(text);
  toastEl.className = 'toast show ' + (k === 'text' ? '' : k);
  clearTimeout(toast._t);
  toast._t = setTimeout(() => toastEl.classList.remove('show'), ms);
}

function openLightbox(src) {
  lightboxImg.src = src;
  lightboxEl.classList.add('show');
}
function closeLightbox() { lightboxEl.classList.remove('show'); }
lightboxEl.addEventListener('click', closeLightbox);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeLightbox();
});

let _autoresizeRafPending = false;
function autoresize() {
  if (_autoresizeRafPending) return;
  _autoresizeRafPending = true;
  requestAnimationFrame(() => {
    _autoresizeRafPending = false;
    msgEl.style.height = 'auto';
    msgEl.style.height = Math.min(msgEl.scrollHeight, 160) + 'px';
  });
}
msgEl.addEventListener('input', autoresize);

function nowStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

function timeTag(created_at) {
  if (!created_at) return '';
  let d;
  try {
    d = new Date(created_at.includes('T') ? created_at : created_at.replace(' ', 'T') + 'Z');
  } catch (_) {
    return '';
  }
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return `${p2(d.getHours())}:${p2(d.getMinutes())}`;
  const yest = new Date(now); yest.setDate(now.getDate() - 1);
  if (d.toDateString() === yest.toDateString()) return '昨天';
  return `${d.getMonth() + 1}/${d.getDate()}`;
}
function p2(n) { return String(n).padStart(2, '0'); }

// ---------- nav rail ----------

$$('[data-toggle="recalls-tray"]').forEach((el) => {
  el.addEventListener('click', () => {
    const show = recallsTray.hasAttribute('hidden');
    if (show) recallsTray.removeAttribute('hidden');
    else recallsTray.setAttribute('hidden', '');
  });
});

if (importChatBtn) {
  importChatBtn.addEventListener('click', () => importChatInput.click());
}

// Rail-icons delegated listener: clicking any rail item other than
// #rail-contacts while in contacts-mode should drop out of contacts-mode.
const railIcons = document.querySelector('.rail-icons');
if (railIcons) {
  railIcons.addEventListener('click', (e) => {
    const li = e.target.closest('li');
    if (!li || li.id === 'rail-contacts' || li.id === 'rail-moments' || li.id === 'rail-settings' || li.id === 'rail-groups' || li.id === 'rail-stories') return;
    if (appShell.classList.contains('contacts-mode')) {
      exitContactsMode();
    }
    if (appShell.classList.contains('moments-mode')) {
      exitMomentsView();
    }
    if (appShell.classList.contains('settings-mode')) {
      exitSettingsView();
    }
    if (appShell.classList.contains('groups-mode')) {
      exitGroupsView();
    }
    if (appShell.classList.contains('group-chat-mode')) {
      exitGroupChat();
    }
    if (appShell.classList.contains('stories-mode')) {
      exitStoriesView();
    }
    if (appShell.classList.contains('story-detail-mode')) {
      exitStoryDetail();
    }
    appShell?.classList.remove('story-chat');
    activeCharIsStory = false;
  });
}

// ---------- mobile layout helpers ----------

const mqPhone = window.matchMedia('(max-width: 640px)');
const isMobile = () => mqPhone.matches;

function enterMobileChat() {
  // Phone layout: tapping a conversation opens the chat pane full-screen.
  if (isMobile()) appShell?.classList.add('mobile-chat-open');
}

function exitMobileChat() {
  appShell?.classList.remove('mobile-chat-open');
}

// Crossing the phone breakpoint back to desktop: drop the mobile-only state.
mqPhone.addEventListener('change', (e) => { if (!e.matches) exitMobileChat(); });

// ---------- hash router (#chat / #contacts / #contacts/<id>) ----------
// Programmatic view changes write the hash via replaceState/pushState (which
// do NOT fire hashchange, so no loops); back/forward and manual URL edits
// fire hashchange/popstate → applyRoute() re-applies the view idempotently.

function parseRoute() {
  const h = location.hash.replace(/^#\/?/, '');
  if (h === 'contacts') return { mode: 'contacts', charId: null };
  const m = h.match(/^contacts\/(.+)$/);
  if (m) {
    let id = m[1];
    try { id = decodeURIComponent(id); } catch (_) { /* keep raw */ }
    return { mode: 'contacts', charId: id };
  }
  return { mode: 'chat', charId: null };
}

function replaceRoute(route, push = false) {
  const url = route.mode === 'contacts'
    ? (route.charId ? `#contacts/${encodeURIComponent(route.charId)}` : '#contacts')
    : location.pathname; // chat view: drop the hash entirely
  if (push) history.pushState(null, '', url);
  else history.replaceState(null, '', url);
}

// Contacts view always lands on a selection: prefer the character whose
// detail is open, else the active chat partner, else the first character.
function pickDefaultContactId() {
  return currentDetailCharId || activeCharId || allCharacters[0]?.id || null;
}

// Shared "enter contacts view" logic used by the rail toggle, applyRoute and
// the initial boot route. Renders the grid; does NOT touch the URL.
function enterContactsView() {
  const shell = appShell;
  if (!shell) return;
  closeChatDetailPanel();
  if (shell.classList.contains('groups-mode')) exitGroupsView();
  if (shell.classList.contains('group-chat-mode')) exitGroupChat();
  if (shell.classList.contains('moments-mode')) exitMomentsView();
  if (shell.classList.contains('settings-mode')) exitSettingsView();
  if (shell.classList.contains('stories-mode')) exitStoriesView();
  if (shell.classList.contains('story-detail-mode')) exitStoryDetail();
  shell.classList.remove('story-chat');
  activeCharIsStory = false;
  shell.classList.add('contacts-mode');
  exitMobileChat();
  $$('.rail-icons li.active').forEach(el => el.classList.remove('active'));
  if (railContacts) railContacts.classList.add('active');
  if (charList) charList.setAttribute('hidden', '');
  if (contactsGrid) contactsGrid.removeAttribute('hidden');
  // The grid renders asynchronously; the selection highlight may have been
  // applied before any card existed — re-apply once cards are in the DOM.
  renderContactsGrid().then(() => {
    if (currentDetailCharId) highlightSelectedCard(currentDetailCharId);
  });
}

function applyRoute() {
  const route = parseRoute();
  const shell = appShell;
  const inContacts = !!shell?.classList.contains('contacts-mode');

  if (route.mode === 'chat') {
    if (inContacts) exitContactsMode();
    return;
  }
  if (!inContacts) enterContactsView();
  if (route.charId) {
    if (route.charId !== currentDetailCharId) {
      const c = allCharacters.find(x => x.id === route.charId);
      if (c && c.card_type === 'story') {
        openStoryDetailFromContacts(route.charId);
      } else {
        openCharDetail(route.charId, { fromRoute: true });
      }
    }
  } else {
    const def = allCharacters[0]?.id;
    if (def) {
      const c = allCharacters[0];
      replaceRoute({ mode: 'contacts', charId: def });
      if (c.card_type === 'story') {
        openStoryDetailFromContacts(def);
      } else {
        openCharDetail(def, { fromRoute: true });
      }
    } else {
      exitContactsMode();
    }
  }
}

window.addEventListener('hashchange', applyRoute);
window.addEventListener('popstate', applyRoute);

// ---------- contacts view (character grid) ----------

const msgRail = document.querySelector('.rail-icons li[title="消息"]');

function exitContactsMode() {
  const shell = appShell;
  if (!shell) return;
  shell.classList.remove('contacts-mode');
  exitMobileChat();
  $$('.rail-icons li.active').forEach(el => el.classList.remove('active'));
  if (msgRail) msgRail.classList.add('active');
  if (contactsGrid) contactsGrid.setAttribute('hidden', '');
  if (charList) charList.removeAttribute('hidden');
  // The detail panel is only meaningful inside contacts-mode. If it was left
  // open when we switch back to the chat view, hide it — otherwise it becomes
  // an unpositioned grid item and breaks the 3-column layout.
  closeCharDetail();
  replaceRoute({ mode: 'chat' });
}

if (railContacts) {
  railContacts.addEventListener('click', () => {
    const isActive = railContacts.classList.contains('active');
    if (isActive) {
      // Toggle off — back to chat view (also closes a left-open detail panel
      // and syncs the hash via exitContactsMode).
      exitContactsMode();
    } else {
      enterContactsView();
      // Contacts view always opens with a selection (list + detail layout):
      // land directly on #contacts/<id> instead of a bare grid.
      if (!currentDetailCharId) {
        const def = pickDefaultContactId();
        if (def) openCharDetail(def, { fromRoute: true });
        else replaceRoute({ mode: 'contacts', charId: null });
      }
    }
  });
}

// ---------- moments view (global timeline of all AI friends' posts) ----------

function enterMomentsView() {
  const shell = appShell;
  if (!shell) return;
  closeChatDetailPanel();
  if (shell.classList.contains('contacts-mode')) exitContactsMode();
  if (shell.classList.contains('settings-mode')) exitSettingsView();
  if (shell.classList.contains('groups-mode')) exitGroupsView();
  if (shell.classList.contains('group-chat-mode')) exitGroupChat();
  if (shell.classList.contains('stories-mode')) exitStoriesView();
  if (shell.classList.contains('story-detail-mode')) exitStoryDetail();
  shell.classList.add('moments-mode');
  exitMobileChat();
  $$('.rail-icons li.active').forEach(el => el.classList.remove('active'));
  if (railMoments) railMoments.classList.add('active');
  const mv = $('#moments-view');
  if (mv) mv.removeAttribute('hidden');
  loadAllMoments();
}

function exitMomentsView() {
  const shell = appShell;
  if (!shell) return;
  shell.classList.remove('moments-mode');
  exitMobileChat();
  $$('.rail-icons li.active').forEach(el => el.classList.remove('active'));
  if (msgRail) msgRail.classList.add('active');
  const mv = $('#moments-view');
  if (mv) mv.setAttribute('hidden', '');
}

function formatMomentTime(s) {
  if (!s) return '';
  const d = new Date(s);
  if (isNaN(d)) return s;
  const diff = Date.now() - d.getTime();
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return Math.floor(diff / 60000) + ' 分钟前';
  if (diff < 86400000) return Math.floor(diff / 3600000) + ' 小时前';
  if (diff < 604800000) return Math.floor(diff / 86400000) + ' 天前';
  return d.toLocaleDateString('zh-CN');
}

let _momentsCache = [];
let _momentsShown = 0;
const MOMENTS_PAGE_SIZE = 20;

async function loadAllMoments() {
  const timeline = $('#moments-timeline');
  if (!timeline) return;
  timeline.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const res = await fetch('/api/moments/all');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    _momentsCache = (await res.json()).moments || [];
    _momentsShown = 0;
    if (!_momentsCache.length) {
      timeline.innerHTML = '<div class="empty">还没有动态</div>';
      return;
    }
    timeline.innerHTML = '';
    renderMomentsPage();
  } catch (e) {
    console.error(e);
    timeline.innerHTML = '<div class="empty">加载失败</div>';
  }
}

function renderMomentsPage() {
  const timeline = $('#moments-timeline');
  if (!timeline) return;
  const existingBtn = document.getElementById('moments-load-more');
  if (existingBtn) existingBtn.remove();
  const end = Math.min(_momentsShown + MOMENTS_PAGE_SIZE, _momentsCache.length);
  for (let i = _momentsShown; i < end; i++) {
    timeline.appendChild(renderMomentItem(_momentsCache[i]));
  }
  _momentsShown = end;
  if (_momentsShown < _momentsCache.length) {
    const btn = document.createElement('button');
    btn.id = 'moments-load-more';
    btn.className = 'load-more-btn';
    btn.textContent = `加载更多（还有 ${_momentsCache.length - _momentsShown} 条）`;
    btn.addEventListener('click', renderMomentsPage);
    timeline.appendChild(btn);
  }
}

function renderMomentItem(m) {
  const item = document.createElement('div');
  item.className = 'mv-item';
  item.dataset.momentId = m.id;

  const isUser = m.author_type === 'user';
  const avatarSrc = isUser
    ? (window.APP?.uid ? makeGradientAvatar(window.APP.uid) : '/static/avatar-placeholder.png')
    : (m.char_avatar || '/static/avatar-placeholder.png');
  const name = isUser ? (window.APP?.uid || '我') : (m.char_name || '未知');

  const avatar = document.createElement('img');
  avatar.className = 'mv-avatar';
  avatar.src = avatarSrc;
  avatar.alt = '';
  avatar.onerror = function() { this.src = '/static/avatar-placeholder.png'; };

  const body = document.createElement('div');
  body.className = 'mv-body';

  const nameEl = document.createElement('div');
  nameEl.className = 'mv-name';
  nameEl.textContent = name;

  const contentEl = document.createElement('div');
  contentEl.className = 'mv-content';
  contentEl.textContent = m.content || '';

  const timeEl = document.createElement('div');
  timeEl.className = 'mv-time';
  timeEl.textContent = formatMomentTime(m.created_at);

  body.append(nameEl, contentEl, timeEl);

  const comments = m.comments || [];
  const likes = m.likes || [];

  if (comments.length || likes.length) {
    const interactions = document.createElement('div');
    interactions.className = 'mv-interactions';

    if (likes.length) {
      const likesEl = document.createElement('div');
      likesEl.className = 'mv-likes';
      const icon = document.createElement('span');
      icon.className = 'mv-like-icon';
      icon.textContent = '\u2764';
      const names = document.createElement('span');
      names.className = 'mv-like-names';
      names.textContent = likes.map(l => l.liker_type === 'user' ? (window.APP?.uid || '我') : (l.char_name || '未知')).join(', ');
      likesEl.append(icon, ' ', names);
      interactions.appendChild(likesEl);
    }

    if (comments.length) {
      const commentsEl = document.createElement('div');
      commentsEl.className = 'mv-comments';
      for (const c of comments) {
        const commentEl = document.createElement('div');
        commentEl.className = 'mv-comment';
        const commenter = document.createElement('span');
        commenter.className = 'mv-commenter';
        commenter.textContent = c.commenter_type === 'user' ? (window.APP?.uid || '我') : (c.char_name || '未知');
        const text = document.createElement('span');
        text.className = 'mv-comment-text';
        text.textContent = ': ' + c.content;
        commentEl.append(commenter, text);
        commentsEl.appendChild(commentEl);
      }
      interactions.appendChild(commentsEl);
    }

    body.appendChild(interactions);
  }

  const actions = document.createElement('div');
  actions.className = 'mv-actions';

  const likeBtn = document.createElement('button');
  likeBtn.className = 'mv-action-btn';
  likeBtn.textContent = '\u2764 点赞';
  likeBtn.addEventListener('click', () => likeMoment(m.id, likeBtn));

  const commentBtn = document.createElement('button');
  commentBtn.className = 'mv-action-btn';
  commentBtn.textContent = '评论';
  commentBtn.addEventListener('click', () => toggleCommentBox(body, m.id));

  actions.append(likeBtn, commentBtn);
  body.appendChild(actions);

  item.append(avatar, body);
  return item;
}

function toggleCommentBox(body, momentId) {
  let box = body.querySelector('.mv-comment-box');
  if (box) {
    box.classList.toggle('active');
    if (box.classList.contains('active')) {
      box.querySelector('input').focus();
    }
    return;
  }
  box = document.createElement('div');
  box.className = 'mv-comment-box active';
  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = '写评论…';
  input.maxLength = 200;
  const btn = document.createElement('button');
  btn.textContent = '发送';
  btn.addEventListener('click', () => commentMoment(momentId, input, box));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') commentMoment(momentId, input, box);
  });
  box.append(input, btn);
  body.appendChild(box);
  input.focus();
}

async function likeMoment(momentId, btn) {
  try {
    const res = await fetch(`/api/moments/${momentId}/likes`, { method: 'POST' });
    if (res.ok) {
      btn.style.color = 'var(--accent, #6FB89E)';
      btn.textContent = '\u2764 已赞';
      btn.disabled = true;
    } else if (res.status === 409) {
      toast('已经点赞过了', 'info', 2000);
    }
  } catch (e) {
    toast('点赞失败', 'error', 2000);
  }
}

async function commentMoment(momentId, inputEl, boxEl) {
  const content = inputEl.value.trim();
  if (!content) return;
  try {
    const res = await fetch(`/api/moments/${momentId}/comments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    if (res.ok) {
      inputEl.value = '';
      boxEl.classList.remove('active');
      const item = document.querySelector(`.mv-item[data-moment-id="${momentId}"]`);
      if (item) {
        let interactions = item.querySelector('.mv-interactions');
        if (!interactions) {
          interactions = document.createElement('div');
          interactions.className = 'mv-interactions';
          item.querySelector('.mv-body')?.appendChild(interactions);
        }
        let commentsEl = interactions.querySelector('.mv-comments');
        if (!commentsEl) {
          commentsEl = document.createElement('div');
          commentsEl.className = 'mv-comments';
          interactions.appendChild(commentsEl);
        }
        const commentEl = document.createElement('div');
        commentEl.className = 'mv-comment';
        const commenter = document.createElement('span');
        commenter.className = 'mv-commenter';
        commenter.textContent = window.APP?.uid || '我';
        const text = document.createElement('span');
        text.className = 'mv-comment-text';
        text.textContent = ': ' + content;
        commentEl.append(commenter, text);
        commentsEl.appendChild(commentEl);
      }
    }
  } catch (e) {
    toast('评论失败', 'error', 2000);
  }
}

async function postUserMoment() {
  const input = $('#mv-input');
  const btn = $('#mv-post-btn');
  if (!input || !btn) return;
  const content = input.value.trim();
  if (!content) return;
  btn.disabled = true;
  try {
    const res = await fetch('/api/moments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    if (res.ok) {
      input.value = '';
      loadAllMoments();
    } else {
      toast('发布失败', 'error', 2000);
    }
  } catch (e) {
    toast('发布失败', 'error', 2000);
  }
  btn.disabled = false;
}

if (railMoments) {
  railMoments.addEventListener('click', () => {
    if (railMoments.classList.contains('active')) exitMomentsView();
    else enterMomentsView();
  });
}

const mvPostBtn = $('#mv-post-btn');
if (mvPostBtn) {
  mvPostBtn.addEventListener('click', postUserMoment);
}
const mvInput = $('#mv-input');
if (mvInput) {
  mvInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      postUserMoment();
    }
  });
}

// ---------- group chat ----------

let currentGroupId = null;
let gcEs = null;

function enterGroupsView() {
  const shell = appShell;
  if (!shell) return;
  closeChatDetailPanel();
  if (shell.classList.contains('contacts-mode')) exitContactsMode();
  if (shell.classList.contains('moments-mode')) exitMomentsView();
  if (shell.classList.contains('settings-mode')) exitSettingsView();
  if (shell.classList.contains('group-chat-mode')) exitGroupChat();
  if (shell.classList.contains('stories-mode')) exitStoriesView();
  if (shell.classList.contains('story-detail-mode')) exitStoryDetail();
  shell.classList.add('groups-mode');
  exitMobileChat();
  $$('.rail-icons li.active').forEach(el => el.classList.remove('active'));
  if (railGroups) railGroups.classList.add('active');
  const gv = $('#groups-view');
  if (gv) gv.removeAttribute('hidden');
  loadGroupsList();
}

function exitGroupsView() {
  const shell = appShell;
  if (!shell) return;
  shell.classList.remove('groups-mode');
  exitMobileChat();
  $$('.rail-icons li.active').forEach(el => el.classList.remove('active'));
  if (msgRail) msgRail.classList.add('active');
  const gv = $('#groups-view');
  if (gv) gv.setAttribute('hidden', '');
  const gcv = $('#group-chat-view');
  if (gcv) gcv.setAttribute('hidden', '');
  currentGroupId = null;
}

function enterGroupChat(groupId, groupName) {
  const shell = appShell;
  if (!shell) return;
  shell.classList.remove('groups-mode');
  shell.classList.add('group-chat-mode');
  const gcv = $('#group-chat-view');
  if (gcv) gcv.removeAttribute('hidden');
  const gv = $('#groups-view');
  if (gv) gv.setAttribute('hidden', '');
  currentGroupId = groupId;
  const nameEl = $('#gc-name');
  if (nameEl) nameEl.textContent = groupName;
  loadGroupMessages(groupId);
}

function exitGroupChat() {
  const shell = appShell;
  if (!shell) return;
  shell.classList.remove('group-chat-mode');
  shell.classList.add('groups-mode');
  const gcv = $('#group-chat-view');
  if (gcv) gcv.setAttribute('hidden', '');
  const gv = $('#groups-view');
  if (gv) gv.removeAttribute('hidden');
  if (gcEs) { gcEs.close(); gcEs = null; }
  currentGroupId = null;
}

async function loadGroupsList() {
  const list = $('#groups-list');
  if (!list) return;
  list.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const res = await fetch('/api/groups');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const groups = (await res.json()).groups || [];
    if (!groups.length) {
      list.innerHTML = '<div class="empty">还没有群聊，点击上方按钮创建</div>';
      return;
    }
    list.innerHTML = '';
    for (const g of groups) {
      const item = document.createElement('div');
      item.className = 'gv-item';
      const avatars = document.createElement('div');
      avatars.className = 'gv-avatars';
      for (const m of (g.members || []).slice(0, 4)) {
        const img = document.createElement('img');
        img.src = m.avatar_path ? '/uploads/' + m.avatar_path : '/static/avatar-placeholder.png';
        img.alt = '';
        img.onerror = function() { this.src = '/static/avatar-placeholder.png'; };
        avatars.appendChild(img);
      }
      const body = document.createElement('div');
      body.className = 'gv-item-body';
      const name = document.createElement('div');
      name.className = 'gv-item-name';
      name.textContent = g.name;
      const members = document.createElement('div');
      members.className = 'gv-item-members';
      const memberNames = (g.members || []).map(m => m.name).join(', ');
      members.textContent = memberNames;
      body.append(name, members);
      item.append(avatars, body);
      item.addEventListener('click', () => enterGroupChat(g.id, g.name));
      list.appendChild(item);
    }
  } catch (e) {
    console.error(e);
    list.innerHTML = '<div class="empty">加载失败</div>';
  }
}

async function loadGroupMessages(groupId) {
  const logEl = $('#gc-log');
  if (!logEl) return;
  logEl.innerHTML = '';
  try {
    const res = await fetch(`/api/groups/${groupId}/messages`);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const msgs = (await res.json()).messages || [];
    for (const m of msgs) {
      renderGroupMessage(m);
    }
    scrollGcBottom();
  } catch (e) {
    console.error(e);
  }
}

function renderGroupMessage(m) {
  const logEl = $('#gc-log');
  if (!logEl) return;
  const row = document.createElement('div');
  row.className = 'gc-msg ' + (m.sender_type === 'user' ? 'gc-msg-user' : 'gc-msg-char');
  if (m.id) row.dataset.msgId = m.id;

  const avatar = document.createElement('img');
  avatar.className = 'gc-msg-avatar';
  if (m.sender_type === 'user') {
    avatar.src = makeGradientAvatar(window.APP?.uid || '我');
  } else {
    avatar.src = m.char_avatar ? '/uploads/' + m.char_avatar : '/static/avatar-placeholder.png';
    avatar.onerror = function() { this.src = '/static/avatar-placeholder.png'; };
  }
  avatar.alt = '';

  const body = document.createElement('div');
  body.className = 'gc-msg-body';
  const name = document.createElement('div');
  name.className = 'gc-msg-name';
  name.textContent = m.sender_type === 'user' ? (window.APP?.uid || '我') : (m.char_name || '角色');
  const bubble = document.createElement('div');
  bubble.className = 'gc-msg-bubble';
  bubble.textContent = m.content || '';

  body.append(name, bubble);
  row.append(avatar, body);
  logEl.appendChild(row);
  return row;
}

let _gcScrollRafPending = false;
function scrollGcBottom() {
  if (_gcScrollRafPending) return;
  _gcScrollRafPending = true;
  requestAnimationFrame(() => {
    _gcScrollRafPending = false;
    const logEl = $('#gc-log');
    if (logEl) logEl.scrollTop = logEl.scrollHeight;
  });
}

function sendGroupMessage() {
  const input = $('#gc-msg');
  if (!input || !currentGroupId) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = '';

  renderGroupMessage({ sender_type: 'user', content: text });
  scrollGcBottom();

  const params = new URLSearchParams({
    message: text,
  });
  const es = new EventSource(`/api/groups/${currentGroupId}/send?${params.toString()}`);
  gcEs = es;

  let currentCharRow = null;
  let currentBubble = null;
  let currentAcc = '';

  es.addEventListener('char_start', (e) => {
    const data = JSON.parse(e.data);
    currentAcc = '';
    currentCharRow = renderGroupMessage({
      sender_type: 'character',
      char_name: data.name,
      char_avatar: null,
      content: '',
    });
    currentCharRow.classList.add('gc-msg-typing');
    currentBubble = currentCharRow.querySelector('.gc-msg-bubble');
    const avatarEl = currentCharRow.querySelector('.gc-msg-avatar');
    if (avatarEl) avatarEl.src = makeGradientAvatar(data.name);
    scrollGcBottom();
  });

  es.addEventListener('token', (e) => {
    const data = JSON.parse(e.data);
    currentAcc += data.t || '';
    if (currentBubble) {
      currentBubble.textContent = currentAcc;
      scrollGcBottom();
    }
  });

  es.addEventListener('char_done', (e) => {
    const data = JSON.parse(e.data);
    if (currentCharRow) {
      currentCharRow.classList.remove('gc-msg-typing');
      if (currentBubble && data.content) {
        currentBubble.textContent = data.content;
      }
    }
    currentCharRow = null;
    currentBubble = null;
    currentAcc = '';
    scrollGcBottom();
  });

  es.addEventListener('done', () => {
    es.close();
    gcEs = null;
  });

  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      if (currentCharRow) {
        currentCharRow.classList.remove('gc-msg-typing');
        if (currentBubble && !currentBubble.textContent) {
          currentBubble.textContent = '...';
        }
      }
      gcEs = null;
    }
  };
}

// group create modal
async function openGroupCreateModal() {
  const modal = $('#group-create-modal');
  const charList = $('#gc-char-list');
  if (!modal || !charList) return;
  charList.innerHTML = '<li class="empty">加载中…</li>';
  modal.showModal();

  try {
    const res = await fetch('/api/characters');
    if (!res.ok) throw new Error('fetch failed');
    const chars = (await res.json()).characters || [];
    charList.innerHTML = '';
    const selected = new Set();
    for (const c of chars) {
      const li = document.createElement('li');
      li.dataset.charId = c.id;
      const img = document.createElement('img');
      img.src = c.avatar_path ? '/uploads/' + c.avatar_path : '/static/avatar-placeholder.png';
      img.alt = '';
      img.onerror = function() { this.src = '/static/avatar-placeholder.png'; };
      const name = document.createElement('span');
      name.className = 'gc-char-name';
      name.textContent = c.name;
      const check = document.createElement('span');
      check.className = 'gc-char-check';
      li.append(img, name, check);
      li.addEventListener('click', () => {
        if (selected.has(c.id)) {
          selected.delete(c.id);
          li.classList.remove('selected');
        } else {
          selected.add(c.id);
          li.classList.add('selected');
        }
      });
      charList.appendChild(li);
    }
    const confirmBtn = $('#gc-create-confirm');
    if (confirmBtn) {
      confirmBtn.onclick = async () => {
        const nameInput = $('#gc-new-name');
        const name = nameInput ? nameInput.value.trim() : '';
        if (!name) { toast('请输入群名', 'warn', 2000); return; }
        if (selected.size < 2) { toast('至少选择2个角色', 'warn', 2000); return; }
        try {
          const res = await fetch('/api/groups', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, character_ids: Array.from(selected) }),
          });
          if (res.ok) {
            modal.close();
            loadGroupsList();
            toast('群聊创建成功', 'info', 2000);
          } else {
            toast('创建失败', 'error', 2000);
          }
        } catch (e) {
          toast('创建失败', 'error', 2000);
        }
      };
    }
  } catch (e) {
    charList.innerHTML = '<li class="empty">加载失败</li>';
  }
}

// event listeners
if (railGroups) {
  railGroups.addEventListener('click', () => {
    if (railGroups.classList.contains('active')) exitGroupsView();
    else enterGroupsView();
  });
}

const gvCreateBtn = $('#gv-create-btn');
if (gvCreateBtn) {
  gvCreateBtn.addEventListener('click', openGroupCreateModal);
}

const gcBackBtn = $('#gc-back');
if (gcBackBtn) {
  gcBackBtn.addEventListener('click', exitGroupChat);
}

const gcComposer = $('#gc-composer');
if (gcComposer) {
  gcComposer.addEventListener('submit', (e) => {
    e.preventDefault();
    sendGroupMessage();
  });
}

const gcMsgInput = $('#gc-msg');
if (gcMsgInput) {
  gcMsgInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendGroupMessage();
    }
  });
  gcMsgInput.addEventListener('input', () => {
    gcMsgInput.style.height = 'auto';
    gcMsgInput.style.height = Math.min(gcMsgInput.scrollHeight, 100) + 'px';
  });
}

// ---------- story cards ----------

let currentStoryId = null;
let storyNpcList = [];

async function enterStoriesView() {
  const shell = appShell;
  if (!shell) return;
  closeChatDetailPanel();
  if (shell.classList.contains('contacts-mode')) exitContactsMode();
  if (shell.classList.contains('moments-mode')) exitMomentsView();
  if (shell.classList.contains('settings-mode')) exitSettingsView();
  if (shell.classList.contains('groups-mode')) exitGroupsView();
  if (shell.classList.contains('group-chat-mode')) exitGroupChat();
  if (shell.classList.contains('story-detail-mode')) exitStoryDetail();
  shell.classList.remove('story-chat');
  activeCharIsStory = false;
  shell.classList.add('stories-mode');
  exitMobileChat();
  $$('.rail-icons li.active').forEach(el => el.classList.remove('active'));
  if (railStories) railStories.classList.add('active');
  const sv = $('#stories-view');
  if (sv) sv.removeAttribute('hidden');
  if (allStories.length) {
    renderStoriesList();
    if (allStories.length) enterStoryDetail(allStories[0].id);
  } else {
    await loadStoriesList();
  }
}

function exitStoriesView() {
  const shell = appShell;
  if (!shell) return;
  shell.classList.remove('stories-mode', 'has-selection', 'story-detail-mode');
  exitMobileChat();
  $$('.rail-icons li.active').forEach(el => el.classList.remove('active'));
  if (msgRail) msgRail.classList.add('active');
  const sv = $('#stories-view');
  if (sv) sv.setAttribute('hidden', '');
  const sdp = $('#story-detail-panel');
  if (sdp) sdp.setAttribute('hidden', '');
  $$('.story-item').forEach(el => el.classList.remove('active'));
  if (typeof stopSdpPoolPoll === 'function') stopSdpPoolPoll();
  if (typeof stopSdpRecaptionPoll === 'function') stopSdpRecaptionPoll();
  currentStoryId = null;
}

function enterStoryDetail(storyId) {
  const shell = appShell;
  if (!shell) return;
  shell.classList.add('has-selection');
  const sdp = $('#story-detail-panel');
  if (sdp) {
    sdp.removeAttribute('hidden');
    sdp.classList.add('loading');
  }
  currentStoryId = storyId;
  $$('.story-item').forEach(el => el.classList.remove('active'));
  const sel = document.querySelector(`.story-item[data-id="${storyId}"]`);
  if (sel) sel.classList.add('active');
  loadStoryDetail(storyId).finally(() => {
    if (sdp) sdp.classList.remove('loading');
  });
}

function exitStoryDetail() {
  const shell = appShell;
  if (!shell) return;
  shell.classList.remove('has-selection', 'story-detail-mode');
  const sdp = $('#story-detail-panel');
  if (sdp) sdp.setAttribute('hidden', '');
  $$('.story-item').forEach(el => el.classList.remove('active'));
  highlightSelectedCard(null);
  if (typeof stopSdpPoolPoll === 'function') stopSdpPoolPoll();
  if (typeof stopSdpRecaptionPoll === 'function') stopSdpRecaptionPoll();
  currentStoryId = null;
  currentDetailCharId = null;
}

let allStories = [];

async function loadStoriesList() {
  const list = $('#stories-list');
  if (!list) return;
  list.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const res = await fetch('/api/stories');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    allStories = (await res.json()).stories || [];
    renderStoriesList();
    if (allStories.length) enterStoryDetail(allStories[0].id);
  } catch (e) {
    list.innerHTML = '<div class="empty">加载失败: ' + escapeHtml(e.message) + '</div>';
  }
}

function renderStoriesList() {
  const list = $('#stories-list');
  if (!list) return;
  const searchEl = $('#sv2-search');
  const genreEl = $('#sv2-genre-filter');
  const statusEl = $('#sv2-status-filter');
  const searchTerm = (searchEl?.value || '').toLowerCase().trim();
  const genreFilter = genreEl?.value || '';
  const statusFilter = statusEl?.value || '';

  let filtered = allStories;
  if (searchTerm) {
    filtered = filtered.filter(s => (s.name || '').toLowerCase().includes(searchTerm));
  }
  if (genreFilter) {
    filtered = filtered.filter(s => (s.genre || '').includes(genreFilter));
  }
  if (statusFilter) {
    filtered = filtered.filter(s => (s.story_status || 'ongoing') === statusFilter);
  }

  if (!filtered.length) {
    list.innerHTML = '<div class="empty">' + (allStories.length ? '没有匹配的故事' : '还没有故事，点击上方按钮创建') + '</div>';
    return;
  }
  list.innerHTML = '';
  filtered.forEach(s => {
    const item = document.createElement('div');
    item.className = 'story-item';
    item.dataset.id = s.id;
    const coverHtml = s.cover_url
      ? `<img class="story-item-cover" src="${s.cover_url}" alt="" />`
      : `<div class="story-item-icon"><span data-i="book" data-s="24"></span></div>`;
    const statusBadge = s.story_status === 'completed'
      ? '<span class="story-status-badge completed">已完成</span>'
      : s.story_status === 'abandoned'
      ? '<span class="story-status-badge abandoned">已放弃</span>'
      : '';
    item.innerHTML = `
      ${coverHtml}
      <div class="story-item-info">
        <div class="story-item-name">${escapeHtml(s.name)} ${statusBadge}</div>
        <div class="story-item-sub">${escapeHtml(s.spec || 'story')}</div>
      </div>
    `;
    item.addEventListener('click', () => enterStoryDetail(s.id));
    list.appendChild(item);
  });
  if (window.Icons) window.Icons.initIcons(list);
}

async function loadStoryDetail(storyId) {
  try {
    const [res, res2] = await Promise.all([
      fetch('/api/stories/' + storyId),
      fetch('/api/characters/' + storyId + '/stats'),
    ]);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    const card = data.card || {};
    storyNpcList = card.npcs || [];

    const form = $('#sdp-form');
    if (form) {
      form.title.value = card.title || data.name || '';
      form.genre.value = card.genre || '';
      form.world_setting.value = card.world_setting || '';
      form.plot_summary.value = card.plot_summary || '';
      form.user_role.value = card.user_role || '';
      form.ai_role.value = card.ai_role || '';
      form.opening.value = card.opening || '';
      form.story_rules.value = card.story_rules || '';
      form.story_prompt.value = card.story_prompt || '';
      form.story_choice_prompt.value = card.story_choice_prompt || '';
      form.initial_spatial.value = card.initial_spatial || '';
      form.initial_mood.value = card.initial_mood || '';
      form.initial_intimacy.value = card.initial_intimacy ?? 0;
    }

    const coverPreview = $('#sdp-cover-preview');
    const coverRemoveBtn = $('#sdp-cover-remove-btn');
    if (coverPreview && coverRemoveBtn) {
      if (data.cover_url) {
        coverPreview.src = data.cover_url;
        coverPreview.removeAttribute('hidden');
        coverRemoveBtn.removeAttribute('hidden');
      } else {
        coverPreview.setAttribute('hidden', '');
        coverRemoveBtn.setAttribute('hidden', '');
        coverPreview.src = '';
      }
    }

    const statusSelect = $('#sdp-status-select');
    if (statusSelect) statusSelect.value = data.story_status || 'ongoing';

    renderNpcList();
    loadStoryMemory(storyId);
    loadStoryTriggers(storyId);

    if (res2.ok) {
      const stats = await res2.json();
      $('#sdp-stat-total').textContent = stats.total ?? '—';
      $('#sdp-stat-first').textContent = stats.first_at ?? '—';
      $('#sdp-stat-last').textContent = stats.last_at ?? '—';
    }
  } catch (e) {
    toast('加载故事失败: ' + e.message, 'error');
  }
}

// ---------- story memory ----------

async function loadStoryMemory(storyId) {
  const listEl = $('#sdp-memory-list');
  if (!listEl) return;
  try {
    const res = await fetch(`/api/stories/${storyId}/memory`);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    const memories = data.memories || [];
    if (!memories.length) {
      listEl.innerHTML = '<div class="empty">还没有故事记忆</div>';
      return;
    }
    listEl.innerHTML = '';
    memories.forEach(m => {
      const item = document.createElement('div');
      item.className = 'sdp-memory-item';
      const typeLabels = { plot: '剧情', choice: '选择', npc_state: 'NPC', world_change: '世界', user_action: '行动' };
      const typeLabel = typeLabels[m.memory_type] || m.memory_type;
      const impStars = '★'.repeat(Math.min(m.importance, 5));
      item.innerHTML = `
        <div class="sdp-memory-meta">
          <span class="sdp-memory-type">${typeLabel}</span>
          <span class="sdp-memory-imp" title="重要度 ${m.importance}/10">${impStars}</span>
          <button type="button" class="sdp-memory-del" title="删除">✕</button>
        </div>
        <div class="sdp-memory-content">${escapeHtml(m.content)}</div>
      `;
      item.querySelector('.sdp-memory-del').addEventListener('click', async () => {
        try {
          const r = await fetch(`/api/stories/${storyId}/memory/${m.id}`, { method: 'DELETE' });
          if (!r.ok) throw new Error('HTTP ' + r.status);
          loadStoryMemory(storyId);
        } catch (e) { toast('删除失败: ' + e.message, 'error'); }
      });
      listEl.appendChild(item);
    });
  } catch (e) {
    listEl.innerHTML = '<div class="empty">加载失败</div>';
  }
}

// ---------- story chapters ----------

async function loadStoryTriggers(storyId) {
  const listEl = $('#sdp-trigger-list');
  if (!listEl) return;
  try {
    const r = await fetch(`/api/stories/${storyId}/triggers`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    const triggers = data.triggers || [];
    if (!triggers.length) {
      listEl.innerHTML = '<div class="empty">还没有情景触发器</div>';
    } else {
      listEl.innerHTML = '';
      triggers.forEach((t) => {
        const item = document.createElement('div');
        item.className = 'sdp-memory-item' + (t.is_active ? '' : ' inactive');
        item.innerHTML = `
          <div class="sdp-trigger-condition"><strong>条件：</strong>${escapeHtml(t.condition_text)}</div>
          <div class="sdp-trigger-text"><strong>触发：</strong>${escapeHtml(t.trigger_text)}</div>
          ${t.last_triggered_at ? `<div class="sdp-trigger-fired">最近触发：${escapeHtml(t.last_triggered_at)}</div>` : ''}
          <div class="sdp-trigger-actions">
            <button type="button" class="sdp-trigger-toggle" data-id="${t.id}" data-active="${t.is_active}">${t.is_active ? '停用' : '启用'}</button>
            <button type="button" class="sdp-trigger-delete danger" data-id="${t.id}">删除</button>
          </div>
        `;
        const toggleBtn = item.querySelector('.sdp-trigger-toggle');
        if (toggleBtn) {
          toggleBtn.addEventListener('click', async () => {
            const newActive = toggleBtn.dataset.active === '1' ? 0 : 1;
            try {
              const r2 = await fetch(`/api/stories/${storyId}/triggers/${t.id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_active: newActive }),
              });
              if (!r2.ok) throw new Error('HTTP ' + r2.status);
              loadStoryTriggers(storyId);
            } catch (e) { toast('操作失败: ' + e.message, 'error'); }
          });
        }
        const deleteBtn = item.querySelector('.sdp-trigger-delete');
        if (deleteBtn) {
          deleteBtn.addEventListener('click', async () => {
            try {
              const r2 = await fetch(`/api/stories/${storyId}/triggers/${t.id}`, { method: 'DELETE' });
              if (!r2.ok) throw new Error('HTTP ' + r2.status);
              loadStoryTriggers(storyId);
            } catch (e) { toast('删除失败: ' + e.message, 'error'); }
          });
        }
        listEl.appendChild(item);
      });
    }
  } catch (e) {
    listEl.innerHTML = '<div class="empty">加载失败</div>';
  }
}

// ---------- story cover ----------

const sdpCoverFile = $('#sdp-cover-file');
const sdpCoverUploadBtn = $('#sdp-cover-upload-btn');
const sdpCoverRemoveBtn = $('#sdp-cover-remove-btn');

if (sdpCoverUploadBtn && sdpCoverFile) {
  sdpCoverUploadBtn.addEventListener('click', () => {
    sdpCoverFile.value = '';
    sdpCoverFile.click();
  });
  sdpCoverFile.addEventListener('change', async () => {
    const file = sdpCoverFile.files[0];
    if (!file || !currentStoryId) return;
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await fetch(`/api/stories/${currentStoryId}/cover/upload`, { method: 'POST', body: fd });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      const preview = $('#sdp-cover-preview');
      if (preview) { preview.src = data.cover_url; preview.removeAttribute('hidden'); }
      if (sdpCoverRemoveBtn) sdpCoverRemoveBtn.removeAttribute('hidden');
      toast('封面已上传', 'success');
      loadStoriesList();
    } catch (e) { toast('上传封面失败: ' + e.message, 'error'); }
  });
}

if (sdpCoverRemoveBtn) {
  sdpCoverRemoveBtn.addEventListener('click', async () => {
    if (!currentStoryId) return;
    try {
      await fetch(`/api/stories/${currentStoryId}/cover`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cover_url: '' }),
      });
      const preview = $('#sdp-cover-preview');
      if (preview) { preview.setAttribute('hidden', ''); preview.src = ''; }
      sdpCoverRemoveBtn.setAttribute('hidden', '');
      loadStoriesList();
    } catch (e) { toast('移除封面失败: ' + e.message, 'error'); }
  });
}

// ---------- story templates ----------

const STORY_TEMPLATES = [
  { id: 'fantasy_adventure', name: '奇幻冒险', genre: '奇幻', description: '在一个魔法世界中展开冒险',
    world_setting: '一个充满魔法与古老传说的世界，大陆上分布着各种种族和王国，远古的邪恶力量正在苏醒。',
    plot_summary: '主角被卷入一场关乎世界命运的冒险，需要寻找传说中的神器来阻止黑暗势力的复苏。',
    user_role: '一名年轻的冒险者，拥有尚未觉醒的特殊能力',
    opening: '*清晨的阳光穿过森林的缝隙，你在一棵古老的橡树下醒来。远处传来隐约的号角声，似乎有什么大事正在发生……*',
    story_rules: '魔法系统基于元素之力，主角的能力会随冒险逐渐觉醒。NPC包括导师、同伴和敌人。',
    npcs: [{ name: '艾琳', description: '神秘的女法师，似乎知道关于你的秘密', personality: '冷静睿智，偶尔流露出温柔' }] },
  { id: 'mystery_detective', name: '悬疑推理', genre: '悬疑', description: '调查一桩离奇的案件',
    world_setting: '现代都市，表面平静的社区中隐藏着不为人知的秘密。',
    plot_summary: '一系列离奇事件接连发生，主角需要抽丝剥茧找出真相。',
    user_role: '一名敏锐的调查者，擅长发现别人忽略的细节',
    opening: '*雨夜，你站在那栋废弃@废弃的旧宅前。门半开着，里面漆黑一片。你知道答案就在里面，但你也知道，进去可能就出不来了……*',
    story_rules: '线索需要逐步发现，不能跳跃推理。NPC有嫌疑人和知情者。',
    npcs: [{ name: '陈警官', description: '负责此案的警官，对你的介入持复杂态度', personality: '严肃正直，但似乎在隐瞒什么' }] },
  { id: 'romance_campus', name: '校园恋爱', genre: '恋爱', description: '在校园中邂逅一段感情',
    world_setting: '一所充满青春气息的大学校园，樱花树下藏着无数故事。',
    plot_summary: '新学期开始，主角在校园中遇到了改变命运的人。',
    user_role: '一名大学生，刚刚转入这所学校',
    opening: '*九月的校园，桂花香气弥漫。你拎着行李走进校门，一个转身，撞上了一个人——书散落一地，你抬起头，对上了一双清澈的眼睛……*',
    story_rules: '感情发展需要自然渐进，不能突兀。NPC包括暗恋对象和朋友。',
    npcs: [{ name: '林晓', description: '你撞到的那个女生，文学系大二', personality: '温柔害羞，但骨子里很倔强' }] },
  { id: 'scifi_space', name: '太空科幻', genre: '科幻', description: '在星际间探索未知',
    world_setting: '人类已进入星际时代，殖民了多个星球，但宇宙深处仍有未知威胁。',
    plot_summary: '主角所在的飞船接收到了来自未知星域的信号，决定前往调查。',
    user_role: '飞船上的工程师，负责维护飞船运作',
    opening: '*警报声突然响起，打破了飞船的寂静。"检测到未知信号源，距离3.7光年。"AI的声音冷静而机械。所有船员都被唤醒了……*',
    story_rules: '科技水平基于近未来设定，没有超光速但有冬眠技术。NPC包括船员和可能的异星生命。',
    npcs: [{ name: '赵舰长', description: '飞船的指挥官，经验丰富', personality: '果断冷静，对船员生命负责' }] },
  { id: 'horror_mansion', name: '恐怖宅邸', genre: '恐怖', description: '在一座诡异的老宅中求生',
    world_setting: '一座建于上世纪的老宅，传闻中发生过不明事件，至今无人敢靠近。',
    plot_summary: '主角因为某种原因不得不在这座宅邸中过夜，夜幕降临后怪事接连发生。',
    user_role: '一名不信邪的年轻人，因为打赌而来到这里',
    opening: '*午夜十二点，老宅的钟声敲响。你坐在客厅的旧沙发上，手电筒的光在墙壁上投射出诡异的影子。突然，楼上响起了脚步声……*',
    story_rules: '恐怖氛围需要逐步升级，不能一开始就过于惊悚。NPC包括宅邸中的"存在"。',
    npcs: [{ name: '???', description: '宅邸中某个不确定的存在', personality: '未知' }] },
  { id: 'daily_life', name: '日常故事', genre: '日常', description: '一段温暖的日常故事',
    world_setting: '一个普通的小镇，生活节奏缓慢而温馨。',
    plot_summary: '主角搬到新小镇开始新生活，遇到了各种有趣的人。',
    user_role: '刚搬到小镇的新居民，开了一家小咖啡馆',
    opening: '*你推开咖啡馆的门，阳光洒在木质地板上。这是你在新小镇的第一天，空气中弥漫着咖啡的香气和陌生人的好奇目光……*',
    story_rules: '故事节奏轻松温暖，没有重大冲突。NPC包括邻居和顾客。',
    npcs: [{ name: '老张', description: '隔壁杂货店的老板，热心肠', personality: '开朗健谈，喜欢八卦' }] },
];

function initStoryTemplates() {
  const select = $('#sv2-template-select');
  if (!select) return;
  STORY_TEMPLATES.forEach(t => {
    const opt = document.createElement('option');
    opt.value = t.id;
    opt.textContent = t.name;
    select.appendChild(opt);
  });
  select.addEventListener('change', () => {
    const tpl = STORY_TEMPLATES.find(t => t.id === select.value);
    if (!tpl) return;
    $('#sv2-new-title').value = tpl.name;
    $('#sv2-new-genre').value = tpl.genre;
    $('#sv2-new-world').value = tpl.world_setting;
    $('#sv2-new-role').value = tpl.user_role;
    $('#sv2-new-opening').value = tpl.opening;
  });
}

// ---------- story search/filter event listeners ----------

const sv2Search = $('#sv2-search');
const sv2GenreFilter = $('#sv2-genre-filter');
const sv2StatusFilter = $('#sv2-status-filter');
if (sv2Search) sv2Search.addEventListener('input', _debounce(renderStoriesList, 200));
if (sv2GenreFilter) sv2GenreFilter.addEventListener('change', renderStoriesList);
if (sv2StatusFilter) sv2StatusFilter.addEventListener('change', renderStoriesList);

// ---------- story memory add button ----------

const sdpMemoryAddBtn = $('#sdp-memory-add-btn');
if (sdpMemoryAddBtn) {
  sdpMemoryAddBtn.addEventListener('click', async () => {
    if (!currentStoryId) return;
    const typeEl = $('#sdp-memory-type');
    const contentEl = $('#sdp-memory-content');
    const impEl = $('#sdp-memory-importance');
    const content = contentEl?.value?.trim();
    if (!content) return;
    try {
      const r = await fetch(`/api/stories/${currentStoryId}/memory`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          memory_type: typeEl?.value || 'plot',
          content,
          importance: parseInt(impEl?.value || '5'),
        }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      if (contentEl) contentEl.value = '';
      loadStoryMemory(currentStoryId);
    } catch (e) { toast('添加记忆失败: ' + e.message, 'error'); }
  });
}

// ---------- story trigger add button ----------

const sdpTriggerAddBtn = $('#sdp-trigger-add-btn');
if (sdpTriggerAddBtn) {
  sdpTriggerAddBtn.addEventListener('click', async () => {
    if (!currentStoryId) return;
    const condEl = $('#sdp-trigger-condition');
    const textEl = $('#sdp-trigger-text');
    const condition = condEl?.value?.trim();
    const trigger = textEl?.value?.trim();
    if (!condition || !trigger) { toast('条件和触发文本都不能为空', 'warn'); return; }
    try {
      const r = await fetch(`/api/stories/${currentStoryId}/triggers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ condition_text: condition, trigger_text: trigger }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      if (condEl) condEl.value = '';
      if (textEl) textEl.value = '';
      loadStoryTriggers(currentStoryId);
    } catch (e) { toast('添加触发器失败: ' + e.message, 'error'); }
  });
}

// ---------- story pool (photo pool for story cards) ----------

const sdpPoolList       = $('#sdp-pool-list');
const sdpPoolFileInput  = $('#sdp-pool-file');
const sdpPoolAddBtn     = $('#sdp-pool-add-btn');
const sdpPoolUploadBtn  = $('#sdp-pool-upload-btn');
const sdpPoolClearBtn   = $('#sdp-pool-clear-btn');
const sdpPoolDeleteAllBtn = $('#sdp-pool-delete-all-btn');
const sdpPoolQueue      = $('#sdp-pool-queue');
const sdpPoolQueueCount = $('#sdp-pool-queue-count');
const sdpPoolStatus     = $('#sdp-pool-upload-status');
const sdpPoolRecaptionAllBtn = $('#sdp-pool-recaption-all-btn');
let sdpPoolQueueFiles = [];
let sdpPoolUploadedKeys = new Set();
let sdpPoolUploadingKeys = new Set();

function renderSdpPool(images) {
  if (!sdpPoolList) return;
  sdpPoolList.innerHTML = '';
  if (!images.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = '还没有照片,先上传一张吧。';
    sdpPoolList.appendChild(li);
    return;
  }
  for (const img of images) {
    const li = document.createElement('li');
    li.className = 'cdp-pool-item';
    li.dataset.id = img.id;

    const wrap = document.createElement('div');
    wrap.className = 'cdp-pool-thumb-wrap';
    const pic = document.createElement('img');
    pic.src = img.thumb_url;
    pic.alt = img.caption || 'photo';
    pic.loading = 'lazy';
    wrap.appendChild(pic);

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'cdp-pool-del';
    del.title = '删除';
    del.setAttribute('aria-label', '删除这张照片');
    del.textContent = '×';
    del.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      deleteSdpPoolItem(currentStoryId, img.id, li);
    });
    wrap.appendChild(del);

    const caption = document.createElement('div');
    caption.className = 'cdp-pool-caption';
    caption.dataset.id = img.id;
    const textSpan = document.createElement('span');
    textSpan.className = 'cdp-pool-caption-text';
    if (img.caption === '无法描述') {
      caption.classList.add('failed');
      caption.title = 'AI 没法描述这张照片。点「编辑描述」手动写一段场景描述';
      textSpan.textContent = '无法描述';
    } else {
      caption.title = '点击文字或「编辑描述」按钮修改';
      textSpan.textContent = img.caption || '正在生成场景描述…';
    }
    caption.appendChild(textSpan);

    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'cdp-pool-caption-edit';
    editBtn.title = '编辑描述';
    editBtn.setAttribute('aria-label', '编辑描述');
    editBtn.textContent = '✎ 编辑描述';
    editBtn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      beginEditSdpCaption(caption, img.id);
    });
    caption.appendChild(editBtn);
    caption.addEventListener('click', () => {
      beginEditSdpCaption(caption, img.id);
    });

    li.appendChild(wrap);
    li.appendChild(caption);
    sdpPoolList.appendChild(li);
  }
}

async function loadStoryPool(storyId) {
  if (!sdpPoolList) return;
  sdpPoolList.innerHTML = '<li class="empty">加载中…</li>';
  try {
    const r = await fetch(`/api/characters/${storyId}/pool`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    renderSdpPool(data.images || []);
  } catch (e) {
    console.error('loadStoryPool failed', e);
    sdpPoolList.innerHTML = '<li class="empty">加载失败,稍后重试</li>';
  }
}

async function deleteAllStoryPool(storyId) {
  const count = sdpPoolList ? sdpPoolList.querySelectorAll('li.cdp-pool-item').length : 0;
  if (!count) return;
  if (!confirm(`确定删除全部 ${count} 张照片吗？删除后不可恢复。`)) return;
  try {
    const r = await fetch(`/api/characters/${storyId}/pool`, { method: 'DELETE' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    toast(`已删除 ${data.deleted} 张照片`, 'success');
    await loadStoryPool(storyId);
  } catch (e) {
    console.error('delete all story pool failed', e);
    toast('删除失败:' + e.message, 'error');
  }
}

async function uploadOneSdpPoolItem(storyId, file) {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch(`/api/characters/${storyId}/pool`, { method: 'POST', body: fd });
  if (!r.ok) {
    const errText = await r.text();
    throw new Error(`HTTP ${r.status}: ${errText.slice(0, 120)}`);
  }
  const data = await r.json();
  const items = sdpPoolList.querySelectorAll('li.empty');
  if (items.length) items.forEach((n) => n.remove());
  const li = document.createElement('li');
  li.className = 'cdp-pool-item';
  li.dataset.id = data.id;
  li.innerHTML =
    '<div class="cdp-pool-thumb-wrap"><img src="' + (data.thumb_url || data.url) +
    '" alt=""></div>' +
    '<div class="cdp-pool-caption"><span class="cdp-pool-caption-text">正在生成场景描述…</span>' +
    '<button type="button" class="cdp-pool-caption-edit" title="编辑描述" aria-label="编辑描述">✎ 编辑描述</button></div>';
  const cap = li.querySelector('.cdp-pool-caption');
  cap.addEventListener('click', () => beginEditSdpCaption(cap, data.id));
  li.querySelector('.cdp-pool-caption-edit').addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    beginEditSdpCaption(cap, data.id);
  });
  sdpPoolList.prepend(li);
  scheduleSdpPoolPoll(storyId, data.id);
}

// Shared caption poller for the story (SDP) pool — mirrors the CDP pattern.
// Previously every uploaded image started its own loop that fetched the FULL
// pool list; uploading N images produced N concurrent list requests. Now we
// track the pool ids still awaiting a caption and poll once per tick for all
// of them.
let _sdpPoolPollTimer = null;
let _sdpPoolPollPending = new Set();

function applySdpPoolCaption(poolId, target) {
  if (!target || !target.caption) return;
  const li = sdpPoolList && sdpPoolList.querySelector(`li[data-id="${poolId}"]`);
  if (!li) return;
  const cap = li.querySelector('.cdp-pool-caption');
  if (cap && !cap.classList.contains('editing')) {
    if (target.caption === '无法描述') {
      cap.classList.add('failed');
      cap.title = 'AI 没法描述这张照片。点「编辑描述」手动写一段场景描述';
    } else {
      cap.classList.remove('failed');
      cap.title = '点击文字或「编辑描述」按钮修改';
    }
    const span = cap.querySelector('.cdp-pool-caption-text');
    if (span) span.textContent = target.caption;
    else cap.textContent = target.caption;
  }
  const img = li.querySelector('img');
  if (img && target.thumb_url) img.src = target.thumb_url;
}

function scheduleSdpPoolPoll(storyId, poolId) {
  _sdpPoolPollPending.add(poolId);
  if (_sdpPoolPollTimer) return;
  let tries = 0;
  const tick = async () => {
    _sdpPoolPollTimer = null;
    if (!_sdpPoolPollPending.size) return;
    tries++;
    try {
      const r = await fetch(`/api/characters/${storyId}/pool`);
      if (r.ok) {
        const data = await r.json();
        const byId = new Map((data.images || []).map((x) => [x.id, x]));
        for (const pid of Array.from(_sdpPoolPollPending)) {
          const target = byId.get(pid);
          if (target && target.caption) {
            applySdpPoolCaption(pid, target);
            _sdpPoolPollPending.delete(pid);
          }
        }
      }
    } catch (_) { /* keep polling */ }
    if (_sdpPoolPollPending.size && tries < POOL_POLL_MAX_TRIES) {
      _sdpPoolPollTimer = setTimeout(tick, POOL_POLL_MS);
    } else if (_sdpPoolPollPending.size) {
      if (sdpPoolStatus) sdpPoolStatus.textContent = '描述生成较慢,稍后会自动出现';
      _sdpPoolPollPending.clear();
    } else if (sdpPoolStatus) {
      sdpPoolStatus.textContent = '';
    }
  };
  _sdpPoolPollTimer = setTimeout(tick, POOL_POLL_MS);
}

function stopSdpPoolPoll() {
  if (_sdpPoolPollTimer) { clearTimeout(_sdpPoolPollTimer); _sdpPoolPollTimer = null; }
  _sdpPoolPollPending.clear();
}

function beginEditSdpCaption(capEl, poolId) {
  if (capEl.classList.contains('editing')) return;
  const textSpan = capEl.querySelector('.cdp-pool-caption-text');
  const editBtn = capEl.querySelector('.cdp-pool-caption-edit');
  const original = textSpan ? textSpan.textContent : (capEl.dataset.original || '');
  const seed = (original === '正在生成场景描述…') ? '' : original;
  capEl.classList.add('editing');
  capEl.title = '回车保存,Shift+回车换行,Esc 取消';
  if (textSpan) textSpan.hidden = true;
  if (editBtn) editBtn.hidden = true;

  const ta = document.createElement('textarea');
  ta.value = seed;
  ta.rows = 2;
  ta.maxLength = 200;
  ta.placeholder = '给这张照片写一句场景描述(影响 AI 何时挑它)';
  capEl.appendChild(ta);
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);

  const setText = (t) => {
    if (textSpan) textSpan.textContent = t;
    else capEl.textContent = t;
    if (editBtn) editBtn.hidden = false;
  };
  let done = false;
  const cancel = () => {
    if (done) return;
    done = true;
    ta.remove();
    capEl.classList.remove('editing');
    capEl.title = '';
    setText(original);
  };
  const commit = async () => {
    if (done) return;
    done = true;
    const newText = ta.value.trim();
    ta.remove();
    capEl.classList.remove('editing');
    capEl.title = '';
    setText(newText || '(空)');
    if (newText === original.trim()) return;
    try {
      const r = await fetch(
        `/api/characters/${currentStoryId}/pool/${poolId}/caption`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ caption: newText }),
        }
      );
      if (!r.ok) {
        const errText = await r.text();
        throw new Error(`HTTP ${r.status}: ${errText.slice(0, 120)}`);
      }
      const data = await r.json();
      if (data.caption === '无法描述') {
        capEl.classList.add('failed');
        capEl.title = 'AI 没法描述这张照片。点「编辑描述」手动写一段场景描述';
      } else {
        capEl.classList.remove('failed');
        capEl.title = '点击文字或「编辑描述」按钮修改';
      }
      setText(data.caption || '(空)');
      if (sdpPoolStatus) {
        sdpPoolStatus.textContent = data.embedding_updated
          ? '已保存,检索排序已更新'
          : '已保存';
      }
    } catch (e) {
      console.error('edit story caption failed', e);
      if (sdpPoolStatus) sdpPoolStatus.textContent = '保存失败:' + e.message;
      setText(original);
    }
  };

  ta.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      commit();
    } else if (ev.key === 'Escape') {
      ev.preventDefault();
      cancel();
    }
  });
  ta.addEventListener('blur', () => {
    if (!done) commit();
  });
}

async function deleteSdpPoolItem(storyId, poolId, li) {
  if (!confirm('确定删除这张照片?')) return;
  try {
    const r = await fetch(`/api/characters/${storyId}/pool/${poolId}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    li.remove();
    if (!sdpPoolList.querySelector('li.cdp-pool-item')) {
      const empty = document.createElement('li');
      empty.className = 'empty';
      empty.textContent = '还没有照片,先上传一张吧。';
      sdpPoolList.appendChild(empty);
    }
  } catch (e) {
    console.error('delete story pool item failed', e);
    toast('删除失败:' + e.message, 'error');
  }
}

function renderSdpPoolQueue() {
  if (!sdpPoolQueue || !sdpPoolQueueCount) return;
  sdpPoolQueueCount.textContent = String(sdpPoolQueueFiles.length);
  const hasItems = sdpPoolQueueFiles.length > 0;
  sdpPoolQueue.hidden = !hasItems;
  if (sdpPoolUploadBtn) sdpPoolUploadBtn.disabled = !hasItems;
  if (sdpPoolClearBtn) sdpPoolClearBtn.disabled = !hasItems;
  sdpPoolQueue.innerHTML = '';
  if (!hasItems) return;
  sdpPoolQueueFiles.forEach((file, idx) => {
    const li = document.createElement('li');
    li.className = 'cdp-pool-item cdp-pool-queue-item';
    const url = URL.createObjectURL(file);
    li.innerHTML =
      '<div class="cdp-pool-thumb-wrap"><img src="' + url + '" alt=""></div>' +
      '<div class="cdp-pool-caption"><span class="cdp-pool-caption-text">' +
      escapeHtml(file.name) + ' · ' + (file.size / 1024).toFixed(1) + ' KB</span></div>' +
      '<button type="button" class="cdp-pool-del" title="从队列移除">✕</button>';
    li.querySelector('img').addEventListener('load', () => URL.revokeObjectURL(url), { once: true });
    li.querySelector('img').addEventListener('error', () => URL.revokeObjectURL(url), { once: true });
    li.querySelector('.cdp-pool-del').addEventListener('click', (ev) => {
      ev.preventDefault();
      sdpPoolQueueFiles.splice(idx, 1);
      renderSdpPoolQueue();
    });
    sdpPoolQueue.appendChild(li);
  });
}

// story pool event listeners
if (sdpPoolAddBtn && sdpPoolFileInput) {
  sdpPoolAddBtn.addEventListener('click', () => {
    sdpPoolFileInput.value = '';
    sdpPoolFileInput.click();
  });
  sdpPoolFileInput.addEventListener('change', () => {
    const files = sdpPoolFileInput.files;
    if (!files || files.length === 0) return;
    const { accepted, dupCount } = _dedupeFiles(
      Array.from(files), sdpPoolQueueFiles, sdpPoolUploadedKeys, sdpPoolUploadingKeys
    );
    for (const f of accepted) sdpPoolQueueFiles.push(f);
    if (dupCount > 0) toast(`跳过 ${dupCount} 张重复图片`, 'info', 3000);
    renderSdpPoolQueue();
  });
}

if (sdpPoolClearBtn) {
  sdpPoolClearBtn.addEventListener('click', () => {
    sdpPoolQueueFiles = [];
    renderSdpPoolQueue();
    if (sdpPoolStatus) sdpPoolStatus.textContent = '';
  });
}

if (sdpPoolDeleteAllBtn) {
  sdpPoolDeleteAllBtn.addEventListener('click', () => {
    if (currentStoryId) deleteAllStoryPool(currentStoryId);
  });
}

if (sdpPoolRecaptionAllBtn) {
  sdpPoolRecaptionAllBtn.addEventListener('click', async () => {
    if (!currentStoryId) return;
    const storyId = currentStoryId;
    sdpPoolRecaptionAllBtn.disabled = true;
    sdpPoolRecaptionAllBtn.textContent = '🔄 正在描述…';
    try {
      const res = await fetch(`/api/characters/${storyId}/pool/recaption-all`, { method: 'POST' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (!data.queued) {
        toast('没有需要重新描述的图片', 'info', 3000);
        sdpPoolRecaptionAllBtn.disabled = false;
        sdpPoolRecaptionAllBtn.textContent = '🔄 重新描述';
        return;
      }
      toast(`正在重新描述 ${data.queued} 张图片…`, 'info', 3000);
      stopSdpRecaptionPoll();
      let polls = 0;
      const maxPolls = 60;  // 60 × 3s = 3min ceiling
      const tick = async () => {
        polls++;
        await refreshSdpPoolCaptionsOnly(storyId);
        if (polls >= maxPolls) {
          stopSdpRecaptionPoll();
          sdpPoolRecaptionAllBtn.disabled = false;
          sdpPoolRecaptionAllBtn.textContent = '🔄 重新描述';
        }
      };
      _sdpRecaptionTimer = setInterval(tick, 3000);
      tick();
    } catch (e) {
      toast('重新描述失败: ' + e.message, 'error', 4000);
      sdpPoolRecaptionAllBtn.disabled = false;
      sdpPoolRecaptionAllBtn.textContent = '🔄 重新描述';
    }
  });
}

// Lightweight SDP recaption poll — patch caption text in place, never rebuild
// the whole grid (that caused image flicker and re-created every listener).
let _sdpRecaptionTimer = null;

function stopSdpRecaptionPoll() {
  if (_sdpRecaptionTimer) { clearInterval(_sdpRecaptionTimer); _sdpRecaptionTimer = null; }
}

async function refreshSdpPoolCaptionsOnly(storyId) {
  try {
    const r = await fetch(`/api/characters/${storyId}/pool`);
    if (!r.ok) return;
    const data = await r.json();
    for (const img of (data.images || [])) applySdpPoolCaption(img.id, img);
  } catch (_) { /* keep polling */ }
}

if (sdpPoolUploadBtn) {
  sdpPoolUploadBtn.addEventListener('click', async () => {
    if (!currentStoryId) return;
    if (sdpPoolQueueFiles.length === 0) return;
    sdpPoolUploadBtn.disabled = true;
    if (sdpPoolStatus) sdpPoolStatus.textContent = '上传中…';
    let ok = 0, fail = 0;
    const uploadedKeys = new Set();
    for (const f of sdpPoolQueueFiles) {
      const key = _fileKey(f);
      sdpPoolUploadingKeys.add(key);
      try {
        await uploadOneSdpPoolItem(currentStoryId, f);
        ok++;
        uploadedKeys.add(key);
        sdpPoolUploadedKeys.add(key);
      } catch (e) {
        console.error('upload story pool item failed', f.name, e);
        fail++;
      } finally {
        sdpPoolUploadingKeys.delete(key);
      }
    }
    sdpPoolQueueFiles = sdpPoolQueueFiles.filter((f) => !uploadedKeys.has(_fileKey(f)));
    renderSdpPoolQueue();
    sdpPoolUploadBtn.disabled = false;
    if (sdpPoolStatus) sdpPoolStatus.textContent = `上传完成: ${ok} 成功, ${fail} 失败`;
  });
}

// ---------- story history ----------

let sdpHistPage = 0;
let sdpHistTotal = 0;
let sdpHistSelected = new Set();
let sdpHistStoryId = null;
const SDP_HIST_PAGE_SIZE = 30;

async function loadStoryHistory(storyId) {
  sdpHistStoryId = storyId;
  sdpHistPage = 0;
  sdpHistSelected.clear();
  await fetchSdpHistoryPage();
}

async function fetchSdpHistoryPage() {
  const listEl = $('#sdp-history-list');
  if (!listEl || !sdpHistStoryId) return;
  listEl.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const offset = sdpHistPage * SDP_HIST_PAGE_SIZE;
    const res = await fetch(`/api/chat/history?character_id=${sdpHistStoryId}&limit=${SDP_HIST_PAGE_SIZE}&offset=${offset}`);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const { messages, total } = await res.json();
    sdpHistTotal = total || 0;
    listEl.innerHTML = '';
    if (!messages.length) {
      listEl.innerHTML = '<div class="empty">没有聊天记录</div>';
      updateSdpHistPager();
      return;
    }
    for (const m of messages) {
      const row = document.createElement('div');
      row.className = 'cdp-hist-row';
      row.dataset.id = m.id;
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'cdp-hist-check';
      cb.checked = sdpHistSelected.has(m.id);
      cb.addEventListener('change', () => {
        if (cb.checked) sdpHistSelected.add(m.id);
        else sdpHistSelected.delete(m.id);
        updateSdpHistSelectionUI();
      });
      const meta = document.createElement('div');
      meta.className = 'cdp-hist-meta';
      const roleTag = document.createElement('span');
      roleTag.className = 'cdp-hist-role ' + m.role;
      roleTag.textContent = m.role === 'user' ? '你' : '故事';
      const time = document.createElement('span');
      time.className = 'cdp-hist-time';
      time.textContent = m.created_at || '';
      meta.append(roleTag, time);
      const content = document.createElement('div');
      content.className = 'cdp-hist-content';
      content.textContent = m.content || '';
      if (m.excluded_from_context) {
        const tag = document.createElement('span');
        tag.className = 'cdp-hist-excluded';
        tag.textContent = '已排除';
        content.appendChild(tag);
      }
      const delBtn = document.createElement('button');
      delBtn.className = 'cdp-hist-del';
      delBtn.textContent = '删除';
      delBtn.addEventListener('click', async () => {
        if (!confirm('确定删除这条消息？')) return;
        try {
          const r = await fetch(`/api/chat/messages/${m.id}?character_id=${sdpHistStoryId}`, { method: 'DELETE' });
          if (!r.ok) { toast('删除失败', 'error'); return; }
          sdpHistSelected.delete(m.id);
          sdpHistTotal = Math.max(0, sdpHistTotal - 1);
          await fetchSdpHistoryPage();
          toast('已删除', 'success');
        } catch (e) { toast('删除失败', 'error'); }
      });
      row.append(cb, meta, content, delBtn);
      listEl.appendChild(row);
    }
    updateSdpHistPager();
    updateSdpHistSelectionUI();
  } catch (e) {
    listEl.innerHTML = '<div class="empty">加载失败: ' + escapeHtml(e.message) + '</div>';
  }
}

function updateSdpHistPager() {
  const prevBtn = $('#sdp-hist-prev');
  const nextBtn = $('#sdp-hist-next');
  const pageInfo = $('#sdp-hist-page-info');
  const totalPages = Math.ceil(sdpHistTotal / SDP_HIST_PAGE_SIZE);
  if (pageInfo) pageInfo.textContent = sdpHistTotal > 0 ? `第 ${sdpHistPage + 1} / ${totalPages} 页 · 共 ${sdpHistTotal} 条` : '';
  if (prevBtn) prevBtn.disabled = sdpHistPage === 0;
  if (nextBtn) nextBtn.disabled = sdpHistPage >= totalPages - 1;
}

function updateSdpHistSelectionUI() {
  const delBtn = $('#sdp-hist-delete-selected');
  const selCount = $('#sdp-hist-selected-count');
  const n = sdpHistSelected.size;
  if (delBtn) delBtn.disabled = n === 0;
  if (selCount) selCount.textContent = n > 0 ? `已选 ${n} 条` : '';
}

function renderNpcList() {
  const container = $('#sdp-npc-list');
  if (!container) return;
  if (!storyNpcList.length) {
    container.innerHTML = '<div class="sdp-npc-empty">还没有 NPC，点击下方添加</div>';
    return;
  }
  container.innerHTML = '';
  storyNpcList.forEach((npc, idx) => {
    const row = document.createElement('div');
    row.className = 'sdp-npc-row';
    row.innerHTML = `
      <div class="sdp-npc-name-row">
        <input class="sdp-npc-name" placeholder="NPC名称" value="${escapeHtml(npc.name || '')}" data-idx="${idx}" />
        <button type="button" class="sdp-npc-del" data-idx="${idx}" title="删除"><span data-i="x" data-s="14"></span></button>
      </div>
      <textarea class="sdp-npc-desc" placeholder="NPC描述" rows="2" data-idx="${idx}">${escapeHtml(npc.description || '')}</textarea>
      <textarea class="sdp-npc-pers" placeholder="NPC性格" rows="1" data-idx="${idx}">${escapeHtml(npc.personality || '')}</textarea>
    `;
    container.appendChild(row);
  });
  if (window.Icons) window.Icons.initIcons(container);

  container.querySelectorAll('.sdp-npc-del').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.idx, 10);
      storyNpcList.splice(idx, 1);
      renderNpcList();
    });
  });
  container.querySelectorAll('.sdp-npc-name').forEach(inp => {
    inp.addEventListener('input', () => {
      const idx = parseInt(inp.dataset.idx, 10);
      storyNpcList[idx].name = inp.value;
    });
  });
  container.querySelectorAll('.sdp-npc-desc').forEach(inp => {
    inp.addEventListener('input', () => {
      const idx = parseInt(inp.dataset.idx, 10);
      storyNpcList[idx].description = inp.value;
    });
  });
  container.querySelectorAll('.sdp-npc-pers').forEach(inp => {
    inp.addEventListener('input', () => {
      const idx = parseInt(inp.dataset.idx, 10);
      storyNpcList[idx].personality = inp.value;
    });
  });
}

function collectNpcList() {
  return storyNpcList.map(n => ({
    name: (n.name || '').trim(),
    description: (n.description || '').trim(),
    personality: (n.personality || '').trim(),
  })).filter(n => n.name);
}

async function saveStoryDetail() {
  if (!currentStoryId) return;
  const form = $('#sdp-form');
  if (!form) return;
  const errEl = $('#sdp-form-error');
  if (errEl) errEl.setAttribute('hidden', '');

  const title = form.title.value.trim();
  if (!title) {
    if (errEl) { errEl.textContent = '故事标题不能为空'; errEl.removeAttribute('hidden'); }
    return;
  }

  const payload = {
    title,
    genre: form.genre.value.trim(),
    world_setting: form.world_setting.value.trim(),
    plot_summary: form.plot_summary.value.trim(),
    user_role: form.user_role.value.trim(),
    ai_role: form.ai_role.value.trim(),
    opening: form.opening.value.trim(),
    story_rules: form.story_rules.value.trim(),
    story_prompt: form.story_prompt.value.trim(),
    story_choice_prompt: form.story_choice_prompt.value.trim(),
    initial_spatial: form.initial_spatial.value.trim(),
    initial_mood: form.initial_mood.value.trim(),
    initial_intimacy: parseInt(form.initial_intimacy.value, 10) || 0,
    npcs: collectNpcList(),
  };

  try {
    const res = await fetch('/api/stories/' + currentStoryId, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'HTTP ' + res.status);
    }
    toast('故事已保存', 'success');
  } catch (e) {
    if (errEl) { errEl.textContent = e.message; errEl.removeAttribute('hidden'); }
    toast('保存失败: ' + e.message, 'error');
  }
}

let activeCharIsStory = false;

function startStoryChat() {
  if (!currentStoryId) return;
  const storyId = currentStoryId;
  const form = $('#sdp-form');
  const title = form?.title?.value?.trim() || '故事';
  const opening = form?.opening?.value?.trim() || '';
  const shell = appShell;
  if (shell) shell.classList.remove('stories-mode', 'has-selection', 'story-detail-mode', 'contacts-mode');
  const sv = $('#stories-view');
  if (sv) sv.setAttribute('hidden', '');
  const sdp = $('#story-detail-panel');
  if (sdp) sdp.setAttribute('hidden', '');
  if (contactsGrid) contactsGrid.setAttribute('hidden', '');
  $$('.rail-icons li.active').forEach(el => el.classList.remove('active'));
  if (msgRail) msgRail.classList.add('active');
  if (typeof stopSdpPoolPoll === 'function') stopSdpPoolPoll();
  if (typeof stopSdpRecaptionPoll === 'function') stopSdpRecaptionPoll();
  currentStoryId = null;
  replaceRoute({ mode: 'chat' });
  selectCharacter(storyId, title, null, true).then(() => {
    activeCharIsStory = true;
    shell?.classList.add('story-chat');
    logEl.querySelectorAll('.bubble.assistant, .bubble.bot').forEach(b => renderStoryChoices(b));
    if (opening && messageCount === 0) {
      msgEl.value = opening;
      composer.requestSubmit();
    }
  });
}

async function selectStoryFromChat(id, name, avatarPath) {
  const shell = appShell;
  if (shell) shell.classList.remove('stories-mode', 'has-selection', 'story-detail-mode');
  const sv = $('#stories-view');
  if (sv) sv.setAttribute('hidden', '');
  const sdp = $('#story-detail-panel');
  if (sdp) sdp.setAttribute('hidden', '');
  $$('.rail-icons li.active').forEach(el => el.classList.remove('active'));
  if (msgRail) msgRail.classList.add('active');
  await selectCharacter(id, name, avatarPath, true);
  activeCharIsStory = true;
  shell?.classList.add('story-chat');
  logEl.querySelectorAll('.bubble.assistant, .bubble.bot').forEach(b => renderStoryChoices(b));
}

async function deleteStory() {
  if (!currentStoryId) return;
  if (!confirm('确定要删除这个故事吗？所有对话记录也将被删除。')) return;
  try {
    const res = await fetch('/api/stories/' + currentStoryId, { method: 'DELETE' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    toast('故事已删除', 'success');
    exitStoryDetail();
    loadStoriesList();
  } catch (e) {
    toast('删除失败: ' + e.message, 'error');
  }
}

function openStoryCreateModal() {
  const modal = $('#story-create-modal');
  if (modal) modal.showModal();
}

async function createStoryConfirm() {
  const title = $('#sv2-new-title').value.trim();
  if (!title) { toast('故事标题不能为空', 'error'); return; }
  const templateSelect = $('#sv2-template-select');
  const tpl = templateSelect && templateSelect.value
    ? STORY_TEMPLATES.find(t => t.id === templateSelect.value)
    : null;
  const payload = {
    title,
    genre: $('#sv2-new-genre').value.trim(),
    world_setting: $('#sv2-new-world').value.trim(),
    user_role: $('#sv2-new-role').value.trim(),
    opening: $('#sv2-new-opening').value.trim(),
    plot_summary: tpl?.plot_summary || '',
    story_rules: tpl?.story_rules || '',
    npcs: tpl?.npcs || [],
  };
  try {
    const res = await fetch('/api/stories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'HTTP ' + res.status);
    }
    const data = await res.json();
    toast('故事已创建', 'success');
    const modal = $('#story-create-modal');
    if (modal) modal.close();
    $('#sv2-new-title').value = '';
    $('#sv2-new-genre').value = '';
    $('#sv2-new-world').value = '';
    $('#sv2-new-role').value = '';
    $('#sv2-new-opening').value = '';
    if (templateSelect) templateSelect.value = '';
    loadStoriesList();
    enterStoryDetail(data.id);
  } catch (e) {
    toast('创建失败: ' + e.message, 'error');
  }
}

function escapeHtml(s) {
  if (!s) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// story event listeners
if (railStories) {
  railStories.addEventListener('click', () => {
    if (railStories.classList.contains('active')) exitStoriesView();
    else enterStoriesView();
  });
}

const sv2CreateBtn = $('#sv2-create-btn');
if (sv2CreateBtn) sv2CreateBtn.addEventListener('click', openStoryCreateModal);

const sv2CreateConfirm = $('#sv2-create-confirm');
if (sv2CreateConfirm) sv2CreateConfirm.addEventListener('click', (e) => {
  e.preventDefault();
  createStoryConfirm();
});

const sdpBackBtn = $('#sdp-back');
if (sdpBackBtn) sdpBackBtn.addEventListener('click', exitStoryDetail);

const sdpSaveBtn = $('#sdp-save');
if (sdpSaveBtn) sdpSaveBtn.addEventListener('click', saveStoryDetail);

const sdpCancelBtn = $('#sdp-cancel');
if (sdpCancelBtn) sdpCancelBtn.addEventListener('click', exitStoryDetail);

const sdpStatusSelect = $('#sdp-status-select');
if (sdpStatusSelect) sdpStatusSelect.addEventListener('change', async () => {
  if (!currentStoryId) return;
  try {
    const r = await fetch(`/api/stories/${currentStoryId}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: sdpStatusSelect.value }),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    toast('故事状态已更新', 'success');
    loadStoriesList();
  } catch (e) { toast('更新状态失败: ' + e.message, 'error'); }
});

const sdpChatBtn = $('#sdp-chat');
if (sdpChatBtn) sdpChatBtn.addEventListener('click', startStoryChat);

const sdpDeleteBtn = $('#sdp-delete');
if (sdpDeleteBtn) sdpDeleteBtn.addEventListener('click', deleteStory);

const sdpNpcAddBtn = $('#sdp-npc-add-btn');
if (sdpNpcAddBtn) sdpNpcAddBtn.addEventListener('click', () => {
  storyNpcList.push({ name: '', description: '', personality: '' });
  renderNpcList();
});

// story tab switching
$$('.sdp-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const view = tab.dataset.sdpTab;
    $$('.sdp-tab').forEach(t => { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
    tab.classList.add('active');
    tab.setAttribute('aria-selected', 'true');
    $$('.sdp-section').forEach(s => {
      s.setAttribute('hidden', s.dataset.sdpView !== view ? '' : '');
      if (s.dataset.sdpView === view) s.removeAttribute('hidden');
    });
    if (view === 'pool' && currentStoryId) loadStoryPool(currentStoryId);
    if (view === 'history' && currentStoryId) loadStoryHistory(currentStoryId);
    if (view === 'memory' && currentStoryId) loadStoryMemory(currentStoryId);
    if (view === 'chapters' && currentStoryId) loadStoryTriggers(currentStoryId);
  });
});

// ---------- settings view (model endpoints + keys) ----------

const SETTINGS_FIELDS = [
  'llm_protocol', 'llm_base_url', 'llm_api_key', 'llm_model',
  'embedding_base_url', 'embedding_api_key', 'embedding_model',
  'tts_base_url', 'tts_api_key', 'tts_model', 'tts_voice_id',
  'image_gen_base_url', 'image_gen_api_key', 'image_gen_model',
  'sleep_time', 'wake_time',
];

async function loadSettingsForm() {
  try {
    const r = await fetch('/api/settings');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const f = settingsForm.elements;
    f.llm_protocol.value = data.llm_protocol || 'anthropic';
    f.llm_base_url.value = data.llm_base_url || '';
    f.llm_api_key.value = '';
    f.llm_model.value = data.llm_model || '';
    f.embedding_base_url.value = data.embedding_base_url || '';
    f.embedding_api_key.value = '';
    f.embedding_model.value = data.embedding_model || '';
    f.tts_base_url.value = data.tts_base_url || '';
    f.tts_api_key.value = '';
    f.tts_model.value = data.tts_model || '';
      f.tts_voice_id.value = data.tts_voice_id || 'female-shaonv';
      f.image_gen_base_url.value = data.image_gen_base_url || '';
      f.image_gen_api_key.value = '';
      f.image_gen_model.value = data.image_gen_model || 'image-01';
      f.sleep_time.value = data.sleep_time || '00:30';
      f.wake_time.value = data.wake_time || '07:30';
      f.sleep_enabled.checked = data.sleep_enabled !== false;
    const hint = document.getElementById('settings-embed-hint');
    if (hint) {
      hint.textContent = data.embed_local_enabled
        ? `当前记忆检索使用本地模型 ${data.embed_model_name || 'bge-m3'},无需远程 key。` +
          '关闭本地模式后此处远程 Embedding 配置生效(需重启服务)。'
        : '当前使用远程 Embedding 服务,改动保存后需重启服务生效。';
    }
  } catch (e) {
    console.error('load settings failed', e);
    toast('加载设置失败:' + e.message, 'error');
  }
}

function enterSettingsView() {
  const shell = appShell;
  if (!shell) return;
  closeChatDetailPanel();
  if (shell.classList.contains('contacts-mode')) exitContactsMode();
  if (shell.classList.contains('moments-mode')) exitMomentsView();
  if (shell.classList.contains('groups-mode')) exitGroupsView();
  if (shell.classList.contains('group-chat-mode')) exitGroupChat();
  if (shell.classList.contains('stories-mode')) exitStoriesView();
  if (shell.classList.contains('story-detail-mode')) exitStoryDetail();
  shell.classList.add('settings-mode');
  exitMobileChat();
  $$('.rail-icons li.active').forEach(el => el.classList.remove('active'));
  if (railSettings) railSettings.classList.add('active');
  if (settingsView) settingsView.removeAttribute('hidden');
  loadSettingsForm();
}

function exitSettingsView() {
  const shell = appShell;
  if (!shell) return;
  shell.classList.remove('settings-mode');
  exitMobileChat();
  $$('.rail-icons li.active').forEach(el => el.classList.remove('active'));
  if (msgRail) msgRail.classList.add('active');
  if (settingsView) settingsView.setAttribute('hidden', '');
}

if (railSettings) {
  railSettings.addEventListener('click', () => {
    if (railSettings.classList.contains('active')) exitSettingsView();
    else enterSettingsView();
  });
}

const settingsSaveBtn = document.getElementById('settings-save');
if (settingsSaveBtn && settingsForm) {
  settingsSaveBtn.addEventListener('click', async () => {
    const body = {};
    let touched = false;
    for (const field of SETTINGS_FIELDS) {
      const value = settingsForm.elements[field].value.trim();
      body[field] = value;
      if (value) touched = true;
    }
    body.sleep_enabled = settingsForm.elements.sleep_enabled.checked;
    touched = true;
    if (!touched) {
      toast('没有需要保存的改动', 'warn');
      return;
    }
    settingsSaveBtn.disabled = true;
    try {
      const r = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const errText = await r.text();
        throw new Error(`HTTP ${r.status}: ${errText.slice(0, 120)}`);
      }
      const data = await r.json();
      toast(
        data.llm_hot_reloaded
          ? '已保存,对话模型配置立即生效'
          : '已保存,部分配置重启服务后生效',
        'success'
      );
    } catch (e) {
      console.error('save settings failed', e);
      toast('保存设置失败:' + e.message, 'error');
    } finally {
      settingsSaveBtn.disabled = false;
    }
  });
}

const settingsCancelBtn = document.getElementById('settings-cancel');
if (settingsCancelBtn) {
  settingsCancelBtn.addEventListener('click', () => exitSettingsView());
}

async function renderContactsGrid() {
  if (!contactsGrid) return;
  contactsGrid.innerHTML = '<div class="empty">加载中…</div>';
  if (!allCharacters || !allCharacters.length) {
    try { await loadCharacters(); } catch (_) {}
  }
  if (!allCharacters || !allCharacters.length) {
    contactsGrid.innerHTML = '<div class="empty">还没有角色,点 ＋ 导入一张吧。</div>';
    return;
  }
  const enriched = allCharacters.map((c) => {
    let card = characterCardCache.get(c.id);
    if (!card) card = {};
    return { ...c, card };
  });
  const uncached = enriched.filter((c) => !characterCardCache.has(c.id));
  if (uncached.length > 0) {
    try {
      const r = await fetch('/api/characters?include_cards=true');
      if (r.ok) {
        const data = await r.json();
        for (const c of (data.characters || [])) {
          if (c.card) characterCardCache.set(c.id, c.card);
        }
        for (const c of enriched) {
          const cached = characterCardCache.get(c.id);
          if (cached) c.card = cached;
        }
      }
    } catch (_) {}
  }
  contactsGrid.innerHTML = '';
  for (const c of enriched) {
    const card = document.createElement('div');
    card.className = 'contact-card';
    card.dataset.id = c.id;
    const isStory = c.card_type === 'story';
    card.innerHTML = `
      <img class="cc-avatar" src="${escapeAttr(c.avatar_path || '/static/avatar-placeholder.png')}" alt="" />
      <button class="cd-del head-btn danger" title="删除${isStory ? '故事' : '角色'}"><span data-i="x" data-s="14"></span></button>
      <div class="cc-meta">
        <div class="cc-name"><span class="cc-name-text"></span>${isStory ? '<span class="story-tag">故事</span>' : '<span class="ai-tag">AI</span>'}</div>
        <div class="cc-desc"></div>
      </div>
    `;
    card.querySelector('.cc-name-text').textContent = c.name;
    const desc = isStory
      ? (c.card.world_setting || c.card.plot_summary || c.card.opening || '').trim()
      : (c.card.basic_info || c.card.description || '').trim();
    card.querySelector('.cc-desc').textContent = desc ? desc.slice(0, 120) : (isStory ? '暂无故事简介' : '暂无简介');
    card.querySelector('.cc-avatar').addEventListener('error', (e) => {
      e.target.src = '/static/avatar-placeholder.png';
    });
    const delBtn = card.querySelector('.cd-del');
    delBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm(`删除${isStory ? '故事' : '角色'}「${c.name}」?${isStory ? '该故事的对话记录也会被清空。' : '该角色的对话记忆也会被清空。'}`)) return;
      try {
        const res = await fetch(`/api/characters/${c.id}`, { method: 'DELETE' });
        if (!res.ok) {
          toast('删除失败,请稍后重试', 'error');
          return;
        }
        if (c.id === activeCharId) {
          stopProactivePolling();
          activeCharId = null;
          activeCharName = '';
          activeCharAvatar = null;
          if (logEl) logEl.innerHTML = '';
          if (recallsEl) recallsEl.innerHTML = '<div class="empty">选择角色开始聊天后,她想起的事情会出现在这里。</div>';
        }
        const wasDetail = c.id === currentDetailCharId;
        if (wasDetail) closeCharDetail();
        await loadCharacters();
        refreshContactsIfVisible();
        if (wasDetail && allCharacters.length) openCharDetail(allCharacters[0].id);
        else if (wasDetail && !allCharacters.length) exitContactsMode();
        toast('已删除', 'success');
      } catch (err) {
        console.error('delete failed', err);
        toast('删除失败,请检查网络', 'error');
      }
    });
    card.addEventListener('click', () => {
      if (c.card_type === 'story') {
        openStoryDetailFromContacts(c.id);
      } else {
        openCharDetail(c.id);
      }
    });
    if (typeof refreshIcons === 'function') refreshIcons(card);
    contactsGrid.appendChild(card);
  }
}

function openStoryDetailFromContacts(storyId) {
  const shell = appShell;
  if (!shell) return;
  currentDetailCharId = storyId;
  replaceRoute({ mode: 'contacts', charId: storyId });
  shell.classList.add('has-selection');
  const cdp = $('#char-detail-panel');
  if (cdp) cdp.setAttribute('hidden', '');
  const sdp = $('#story-detail-panel');
  if (sdp) {
    sdp.removeAttribute('hidden');
    sdp.classList.add('loading');
  }
  currentStoryId = storyId;
  highlightSelectedCard(storyId);
  loadStoryDetail(storyId).finally(() => {
    if (sdp) sdp.classList.remove('loading');
  });
}

// HTML-attribute escape (used for src= URLs)
function escapeAttr(s) {
  return String(s).replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

// ---------- character detail modal ----------

function formatDate(s) {
  if (!s) return '—';
  let iso = s;
  if (!s.includes('T')) iso = s.replace(' ', 'T') + 'Z';
  const d = new Date(iso);
  if (isNaN(+d)) return s;
  const Y = d.getFullYear();
  const M = String(d.getMonth() + 1).padStart(2, '0');
  const D = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${Y}-${M}-${D} ${hh}:${mm}`;
}

function formatMemoryTime(s) {
  if (!s) return '';
  let iso = s;
  if (!s.includes('T')) iso = s.replace(' ', 'T') + 'Z';
  const d = new Date(iso);
  if (isNaN(+d)) return s;
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (sameDay) return `今天 ${hh}:${mm}`;
  const diffDays = Math.round((now - d) / 86400000);
  if (diffDays === 1) return `昨天 ${hh}:${mm}`;
  if (diffDays > 1 && diffDays < 7) return `${diffDays} 天前`;
  const M = String(d.getMonth() + 1).padStart(2, '0');
  const D = String(d.getDate()).padStart(2, '0');
  return `${M}-${D} ${hh}:${mm}`;
}

async function openCharDetail(charId, opts = {}) {
  if (!charDetailPanel) return;
  currentDetailCharId = charId;
  if (!opts.fromChat) {
    replaceRoute({ mode: 'contacts', charId }, !opts.fromRoute);
  }
  appShell?.classList.add('has-selection');
  const sdp = $('#story-detail-panel');
  if (sdp) sdp.setAttribute('hidden', '');
  if (typeof stopSdpPoolPoll === 'function') stopSdpPoolPoll();
  if (typeof stopSdpRecaptionPoll === 'function') stopSdpRecaptionPoll();
  currentStoryId = null;
  switchCdpTab('settings');

  // Reset form placeholders + panel state
  if (cdpFormError) { cdpFormError.hidden = true; cdpFormError.textContent = ''; }
  if (cdpForm) cdpForm.reset();
  if (cdpAvatarPrev) cdpAvatarPrev.src = '/static/avatar-placeholder.png';
  cdpPendingAvatarFile = null;
  if (cdpPreviewUrl) { URL.revokeObjectURL(cdpPreviewUrl); cdpPreviewUrl = null; }
  cdpEls.memories.innerHTML = '<div class="empty">加载中…</div>';

  // 3.5) Image pool — lazy-loaded when the panel first opens.
  loadCharPool(charId);
  cdpEls.statTotal.textContent = cdpEls.statUser.textContent = cdpEls.statBot.textContent =
    cdpEls.statChars.textContent = cdpEls.statFirst.textContent = cdpEls.statLast.textContent = '…';
  charDetailPanel.removeAttribute('hidden');
  highlightSelectedCard(charId);

  // 1) Basic profile + form fields
  let d;
  try {
    const r = await fetch(`/api/characters/${charId}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    d = await r.json();
    characterCardCache.set(charId, d.card || {});
  } catch (e) {
    console.error('openCharDetail profile failed', e);
    toast('加载角色资料失败', 'error');
    charDetailPanel.setAttribute('hidden', '');
    return;
  }

  fillCdpForm(d);

  // 加载关系类型
  fetch(`/api/characters/${charId}/relationship`).then(r => r.json()).then(rd => {
    const sel = document.getElementById('cdp-relationship');
    if (sel) sel.value = rd.relationship || '陌生人';
  }).catch(() => {});

  // 2) Stats (parallel, fire-and-forget)
  fetch(`/api/characters/${charId}/stats`).then(r => r.json()).then(s => {
    cdpEls.statTotal.textContent = s.total;
    cdpEls.statUser.textContent  = s.user_count;
    cdpEls.statBot.textContent   = s.assistant_count;
    cdpEls.statChars.textContent = s.total_chars;
    cdpEls.statFirst.textContent = s.first_ts ? formatDate(s.first_ts) : '—';
    cdpEls.statLast.textContent  = s.last_ts  ? formatDate(s.last_ts)  : '—';
  }).catch(err => {
    console.error('stats fetch failed', err);
    cdpEls.statTotal.textContent = '0';
    cdpEls.statUser.textContent = cdpEls.statBot.textContent =
      cdpEls.statChars.textContent = cdpEls.statFirst.textContent =
      cdpEls.statLast.textContent = '—';
  });

  // 3) Long-term memories (parallel) — browse-mode listing ordered by recency.
  //    We fetch via the dedicated /api/memory/files endpoint so the user sees
  //    the most recent memories regardless of vector similarity. Fall back to
  //    the similarity-ranked /api/memory/recall only if the listing endpoint
  //    returns nothing.
  const renderEmpty = () => {
    cdpEls.memories.innerHTML = '<div class="empty">她现在还没想起什么～</div>';
  };
  const renderGroups = (files) => {
    if (!files.length) { renderEmpty(); return; }
    cdpEls.memories.innerHTML = '';
    for (const f of files) {
      const group = document.createElement('div');
      group.className = 'memory-group';
      group.dataset.id = f.id;

      const head = document.createElement('div');
      head.className = 'memory-group-head';
      const title = document.createElement('span');
      title.className = 'memory-group-title';
      title.textContent = f.description || '一段对话';
      title.title = f.description || '';
      const ts = document.createElement('span');
      ts.className = 'memory-group-ts';
      ts.textContent = formatMemoryTime(f.created_at);
      const score = document.createElement('span');
      score.className = 'memory-group-score';
      score.textContent = `相关度 ${(f.score || 1).toFixed(2)}`;

      const actions = document.createElement('span');
      actions.className = 'memory-group-actions';
      const editBtn = document.createElement('button');
      editBtn.type = 'button';
      editBtn.className = 'mg-action mg-edit';
      editBtn.title = '编辑';
      editBtn.setAttribute('aria-label', '编辑记忆');
      editBtn.innerHTML = ic('pencil');

      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.className = 'mg-action mg-delete';
      delBtn.title = '删除';
      delBtn.setAttribute('aria-label', '删除记忆');
      delBtn.innerHTML = ic('trash');

      editBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        openMemEditModal(f, charId);
      });
      delBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        confirmDeleteMemory(f, charId);
      });

      actions.appendChild(editBtn);
      actions.appendChild(delBtn);

      head.appendChild(title);
      head.appendChild(ts);
      head.appendChild(score);
      head.appendChild(actions);
      group.appendChild(head);

      const body = document.createElement('div');
      body.className = 'memory-group-body';
      const raw = (f.content || '').trim();
      if (raw) {
        const line = document.createElement('div');
        line.className = 'memory-line';
        if (/^你说[::]\s*/.test(raw)) {
          line.classList.add('who-user');
          line.textContent = raw.replace(/^你说[::]\s*/, '你：');
        } else if (/^她回[::]\s*/.test(raw)) {
          line.classList.add('who-bot');
          line.textContent = raw.replace(/^她回[::]\s*/, '她：');
        } else if (/^user\s*:/i.test(raw)) {
          line.classList.add('who-user');
          line.textContent = '你：' + raw.replace(/^user\s*:\s*/i, '');
        } else if (/^assistant\s*:/i.test(raw)) {
          line.classList.add('who-bot');
          line.textContent = '她：' + raw.replace(/^assistant\s*:\s*/i, '');
        } else {
          line.classList.add('who-narration');
          line.textContent = raw;
        }
        body.appendChild(line);
      }
      group.appendChild(body);
      cdpEls.memories.appendChild(group);
    }
  };

  fetch(`/api/memory/files?character_id=${charId}&limit=20`)
    .then((r) => r.json())
    .then((m) => {
      if (m.warning) console.warn('memory files:', m.warning);
      if (m.files && m.files.length) { renderGroups(m.files); return; }
      // fallback: similarity-ranked recall
      return fetch(`/api/memory/recall?character_id=${charId}`).then((r2) => r2.json());
    })
    .then((m2) => {
      if (!m2) return;
      if (m2.warning) console.warn('memory recall:', m2.warning);
      const files = (m2.files || []).map((f) => ({
        ...f,
        score: (m2.segments || []).find((s) => s.recall_file_id === f.id)?.score || 0,
      }));
      renderGroups(files);
    })
    .catch((err) => {
      console.error('memory fetch failed', err);
      renderEmpty();
    });
}

function fillCdpForm(d) {
  if (!cdpForm) return;
  const card = d.card || {};
  cdpForm.elements.name.value = d.name || '';
  cdpForm.elements.nickname.value = card.nickname || '';
  cdpForm.elements.basic_info.value = card.basic_info || card.description || '';
  cdpForm.elements.personality.value = card.personality || '';
  cdpForm.elements.love_values.value = card.love_values || '';
  cdpForm.elements.background.value = card.background || '';
  cdpForm.elements.habits.value = card.habits || '';
  cdpForm.elements.speech_style.value = card.speech_style || '';
  cdpForm.elements.scenario.value   = card.scenario   || '';
  cdpForm.elements.first_mes.value  = card.first_mes  || '';
  cdpForm.elements.mes_example.value = card.mes_example || '';
  cdpForm.elements.system_prompt.value = card.system_prompt || '';
  cdpForm.elements.post_history_instructions.value = card.post_history_instructions || '';
  cdpForm.elements.behaviour_rules.value = card.behaviour_rules
    || DEFAULT_BEHAVIOUR_RULES.replaceAll('{char}', card.nickname || d.name || 'Character');

  const circ = card.circadian_overrides || {};
  for (const k of ['late_night','early_morning','morning','noon','afternoon','evening']) {
    const el = cdpForm.elements['circ_' + k];
    if (el) el.value = circ[k] || DEFAULT_CIRCADIAN[k];
  }
  const emo = card.emotion_overrides || {};
  for (const k of ['sad','happy','angry','anxious']) {
    const wEl = cdpForm.elements['emo_' + k + '_words'];
    const pEl = cdpForm.elements['emo_' + k + '_prompt'];
    const grp = emo[k] || {};
    const def = DEFAULT_EMOTION[k];
    if (wEl) wEl.value = Array.isArray(grp.words) ? grp.words.join(', ') : (grp.words || def.words);
    if (pEl) pEl.value = grp.prompt || def.prompt;
  }
  cdpForm.elements.alternate_greetings.value = Array.isArray(card.alternate_greetings)
    ? card.alternate_greetings.join(', ') : '';

  if (cdpAvatarPrev) {
    cdpAvatarPrev.src = d.avatar_path || '/static/avatar-placeholder.png';
    cdpAvatarPrev.onerror = () => { cdpAvatarPrev.src = '/static/avatar-placeholder.png'; };
  }
}

function showCdpError(msg) {
  if (!cdpFormError) { toast(msg, 'error', 4000); return; }
  cdpFormError.textContent = msg;
  cdpFormError.hidden = false;
}

function closeCharDetail() {
  if (!charDetailPanel) return;
  charDetailPanel.setAttribute('hidden', '');
  const sdp = $('#story-detail-panel');
  if (sdp) sdp.setAttribute('hidden', '');
  if (typeof stopCdpPoolPoll === 'function') stopCdpPoolPoll();
  if (typeof stopCdpRecaptionPoll === 'function') stopCdpRecaptionPoll();
  if (typeof stopSdpPoolPoll === 'function') stopSdpPoolPoll();
  if (typeof stopSdpRecaptionPoll === 'function') stopSdpRecaptionPoll();
  highlightSelectedCard(null);
  currentDetailCharId = null;
  currentStoryId = null;
  appShell?.classList.remove('has-selection');
  closeMemEditModal();
}

// ---------- memory edit modal + delete confirm ----------

let memEditModal = null;
let memEditTarget = null;        // {id, charId, description, content, node}

function ensureMemEditModal() {
  if (memEditModal) return memEditModal;
  const modal = document.createElement('div');
  modal.id = 'mem-edit-modal';
  modal.className = 'mem-edit-modal';
  modal.hidden = true;
  modal.innerHTML = `
    <div class="mem-edit-card" role="dialog" aria-modal="true" aria-labelledby="mem-edit-title">
      <header class="mem-edit-head">
        <h4 id="mem-edit-title">编辑记忆</h4>
        <button type="button" class="mem-edit-close" data-mem-edit-cancel aria-label="关闭">
          <span data-i="close" data-s="16"></span>
        </button>
      </header>
      <p class="mem-edit-hint">主题只在 tab 里显示;内容决定她下次聊起相关话题时能不能想起来。</p>
      <label class="mem-edit-field">
        <span>主题</span>
        <textarea id="mem-edit-desc" rows="1" maxlength="120"></textarea>
      </label>
      <label class="mem-edit-field">
        <span>内容</span>
        <textarea id="mem-edit-content" rows="6"></textarea>
      </label>
      <div id="mem-edit-error" class="mem-edit-error" hidden></div>
      <footer class="mem-edit-foot">
        <button type="button" class="mem-edit-btn" data-mem-edit-cancel>取消</button>
        <button type="button" class="mem-edit-btn primary" data-mem-edit-save>保存</button>
      </footer>
    </div>
  `;
  document.body.appendChild(modal);
  // Close handlers: dim click + any [data-mem-edit-cancel] + Esc.
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeMemEditModal();
  });
  modal.querySelectorAll('[data-mem-edit-cancel]').forEach((btn) =>
    btn.addEventListener('click', (e) => { e.stopPropagation(); closeMemEditModal(); })
  );
  modal.querySelector('[data-mem-edit-save]').addEventListener('click', (e) => {
    e.stopPropagation();
    submitMemEdit();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.hidden) closeMemEditModal();
  });
  // Init inline-SVG icons for the close button (it uses data-i so initIcons handles it).
  if (typeof refreshIcons === 'function') refreshIcons(modal);
  memEditModal = modal;
  return modal;
}

function openMemEditModal(file, charId) {
  const modal = ensureMemEditModal();
  memEditTarget = {
    id: file.id,
    charId,
    description: file.description || '',
    content: file.content || '',
    node: document.querySelector(`.memory-group[data-id="${file.id}"]`),
  };
  modal.querySelector('#mem-edit-desc').value = file.description || '';
  modal.querySelector('#mem-edit-content').value = file.content || '';
  modal.querySelector('#mem-edit-error').hidden = true;
  modal.querySelector('[data-mem-edit-save]').disabled = false;
  modal.hidden = false;
  setTimeout(() => modal.querySelector('#mem-edit-desc').focus(), 30);
}

function closeMemEditModal() {
  if (!memEditModal) return;
  memEditModal.hidden = true;
  memEditTarget = null;
}

async function submitMemEdit() {
  if (!memEditTarget || !memEditModal) return;
  const target = memEditTarget;
  const desc = memEditModal.querySelector('#mem-edit-desc').value.trim();
  const content = memEditModal.querySelector('#mem-edit-content').value.trim();
  const errEl = memEditModal.querySelector('#mem-edit-error');
  const saveBtn = memEditModal.querySelector('[data-mem-edit-save]');

  if (!desc && !content) {
    errEl.textContent = '主题和内容至少填一个';
    errEl.hidden = false;
    return;
  }
  if (desc === target.description && content === target.content) {
    closeMemEditModal();
    return;
  }

  saveBtn.disabled = true;
  try {
    const payload = { character_id: target.charId };
    if (desc !== target.description) payload.description = desc;
    if (content !== target.content) payload.content = content;
    const r = await fetch(`/api/memory/files/${target.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const text = await r.text();
      throw new Error(text || `HTTP ${r.status}`);
    }
    const result = await r.json();
    // Update in-place: title + body text content (no full re-render flicker).
    if (target.node && target.node.isConnected) {
      const titleEl = target.node.querySelector('.memory-group-title');
      const lineEl = target.node.querySelector('.memory-line');
      if (titleEl) {
        titleEl.textContent = result.description || '一段对话';
        titleEl.title = result.description || '';
      }
      if (lineEl) {
        const raw = (result.content || '').trim();
        lineEl.classList.remove('who-user', 'who-bot', 'who-narration');
        if (/^你说[::]\s*/.test(raw)) {
          lineEl.classList.add('who-user');
          lineEl.textContent = raw.replace(/^你说[::]\s*/, '你：');
        } else if (/^她回[::]\s*/.test(raw)) {
          lineEl.classList.add('who-bot');
          lineEl.textContent = raw.replace(/^她回[::]\s*/, '她：');
        } else if (/^user\s*:/i.test(raw)) {
          lineEl.classList.add('who-user');
          lineEl.textContent = '你：' + raw.replace(/^user\s*:\s*/i, '');
        } else if (/^assistant\s*:/i.test(raw)) {
          lineEl.classList.add('who-bot');
          lineEl.textContent = '她：' + raw.replace(/^assistant\s*:\s*/i, '');
        } else {
          lineEl.classList.add('who-narration');
          lineEl.textContent = raw;
        }
      }
    }
    toast(
      result.embedding_updated ? '已更新 · 重新计算了向量' : '已更新',
      'success',
    );
    closeMemEditModal();
  } catch (err) {
    console.error('mem edit failed', err);
    errEl.textContent = `保存失败:${err.message || err}`;
    errEl.hidden = false;
    saveBtn.disabled = false;
  }
}

async function confirmDeleteMemory(file, charId) {
  const preview = (file.description || file.content || '').slice(0, 60);
  if (!confirm(`确定删除这条记忆?\n\n"${preview}${preview.length >= 60 ? '…' : ''}"\n\n她将不再想起这段对话。`)) {
    return;
  }
  const node = document.querySelector(`.memory-group[data-id="${file.id}"]`);
  try {
    const r = await fetch(
      `/api/memory/files/${file.id}?character_id=${encodeURIComponent(charId)}`,
      { method: 'DELETE' },
    );
    if (!r.ok) {
      const text = await r.text();
      throw new Error(text || `HTTP ${r.status}`);
    }
    if (node && node.parentNode) {
      node.classList.add('memory-group-leaving');
      setTimeout(() => node.remove(), 180);
    }
    toast('已删除', 'success');
  } catch (err) {
    console.error('mem delete failed', err);
    toast(`删除失败:${err.message || err}`, 'error');
  }
}

function highlightSelectedCard(charId) {
  if (!contactsGrid) return;
  $$('.contact-card.selected').forEach(el => el.classList.remove('selected'));
  if (!charId) return;
  const card = contactsGrid.querySelector(`.contact-card[data-id="${charId}"]`);
  if (card) card.classList.add('selected');
}

// Re-render the contacts grid if it is currently visible (contacts-mode active).
// Use after any character mutation so the grid reflects the new server state.
function refreshContactsIfVisible() {
  if (!contactsGrid) return;
  const active = appShell?.classList.contains('contacts-mode');
  if (!active) return;
  if (charDetailPanel && !charDetailPanel.hasAttribute('hidden')) {
    // Keep current selection after re-render
    const keep = currentDetailCharId;
    renderContactsGrid().then(() => { if (keep) highlightSelectedCard(keep); });
  } else {
    renderContactsGrid();
  }
}

if (cdpAvatarInput) {
  cdpAvatarInput.addEventListener('change', () => {
    const f = cdpAvatarInput.files && cdpAvatarInput.files[0];
    if (!f) return;
    cdpPendingAvatarFile = f;
    if (cdpPreviewUrl) URL.revokeObjectURL(cdpPreviewUrl);
    cdpPreviewUrl = URL.createObjectURL(f);
    cdpAvatarPrev.src = cdpPreviewUrl;
  });
}

// ---------- panel tabs (基础资料 / 高级字段 / 长期记忆) ----------

const cdpTabBtns = $$('.cdp-tab');
const cdpViews = {};
$$('.cdp-views [data-cdp-view]').forEach((v) => { cdpViews[v.dataset.cdpView] = v; });

function switchCdpTab(name) {
  cdpTabBtns.forEach((t) => {
    const on = t.dataset.cdpTab === name;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  for (const [key, el] of Object.entries(cdpViews)) {
    if (key === name) el.removeAttribute('hidden');
    else el.setAttribute('hidden', '');
  }
  if (name === 'milestones') loadCdpMilestones();
  if (name === 'news') loadCdpNews();
  if (name === 'moments') loadCdpMoments();
  if (name === 'history') loadCdpHistory();
  if (name === 'evolution') loadCdpEvolution();
  if (name === 'scenes') loadCdpScenes();
  if (name === 'rules') loadCdpRules();
  if (name === 'runtime') loadCdpRuntime();
}

async function loadCdpMoments() {
  const el = document.getElementById('cdp-moments');
  if (!el || !currentDetailCharId) return;
  el.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const r = await fetch(`/api/characters/${currentDetailCharId}/moments`);
    if (!r.ok) throw new Error('fetch failed');
    const data = await r.json();
    const moms = data.moments || [];
    if (!moms.length) {
      el.innerHTML = '<div class="empty">还没有动态，和她聊几句吧</div>';
      return;
    }
    el.innerHTML = '';
    for (const m of moms) {
      const item = document.createElement('div');
      item.className = 'cdp-moment';
      const text = document.createElement('div');
      text.className = 'cdp-moment-text';
      text.textContent = m.content;
      const time = document.createElement('div');
      time.className = 'cdp-moment-time';
      time.textContent = m.created_at;
      item.append(text, time);
      el.append(item);
    }
  } catch (e) {
    el.innerHTML = '<div class="empty">加载失败</div>';
  }
}

async function loadCdpNews() {
  const el = document.getElementById('cdp-news');
  if (!el || !currentDetailCharId) return;
  el.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const r = await fetch(`/api/characters/${currentDetailCharId}/news-digests`);
    if (!r.ok) throw new Error('fetch failed');
    const data = await r.json();
    const digests = data.digests || [];
    if (!digests.length) {
      el.innerHTML = '<div class="empty">还没有新闻摘要，和她聊几句吧</div>';
      return;
    }
    el.innerHTML = '';
    for (const d of digests) {
      const item = document.createElement('div');
      item.className = 'cdp-news-item';
      const date = document.createElement('div');
      date.className = 'cdp-news-date';
      date.textContent = d.digest_date;
      const summary = document.createElement('div');
      summary.className = 'cdp-news-summary';
      summary.textContent = d.summary;
      item.append(date, summary);
      el.append(item);
    }
  } catch (e) {
    el.innerHTML = '<div class="empty">加载失败</div>';
  }
}

async function loadCdpMilestones() {
  const el = document.getElementById('cdp-milestones');
  if (!el || !currentDetailCharId) return;
  el.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const r = await fetch(`/api/characters/${currentDetailCharId}/milestones`);
    if (!r.ok) throw new Error('fetch failed');
    const data = await r.json();
    const ms = data.milestones || [];
    if (!ms.length) {
      el.innerHTML = '<div class="empty">还没有重要记忆，继续和她聊天吧</div>';
      return;
    }
    el.innerHTML = '';
    const timeline = document.createElement('div');
    timeline.className = 'milestone-timeline';
    for (const m of ms) {
      const item = document.createElement('div');
      item.className = 'milestone-item';
      const dot = document.createElement('div');
      dot.className = 'milestone-dot';
      const body = document.createElement('div');
      body.className = 'milestone-body';
      const title = document.createElement('div');
      title.className = 'milestone-title';
      title.textContent = m.title;
      const desc = document.createElement('div');
      desc.className = 'milestone-desc';
      desc.textContent = m.description || '';
      const time = document.createElement('div');
      time.className = 'milestone-time';
      time.textContent = m.created_at;
      body.append(title);
      if (m.description) body.append(desc);
      body.append(time);
      item.append(dot, body);
      timeline.append(item);
    }
    el.append(timeline);
  } catch (e) {
    el.innerHTML = '<div class="empty">加载失败</div>';
  }
}

// ---------- custom scenes (in character detail panel) ----------
async function loadCdpScenes() {
  if (!currentDetailCharId) return;
  const listEl = document.getElementById('cdp-scene-list');
  const selEl = document.getElementById('cdp-scene-active-select');
  if (!listEl || !selEl) return;
  listEl.innerHTML = '<li class="empty">加载中…</li>';
  try {
    const r = await fetch(`/api/characters/${currentDetailCharId}/scenes`);
    if (!r.ok) throw new Error('fetch failed');
    const data = await r.json();
    const scenes = data.scenes || [];
    const activeId = data.active_scene_id || '';
    // 渲染激活选择
    selEl.innerHTML = '<option value="">（使用内置时间场景）</option>';
    for (const s of scenes) {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.name + (s.enabled ? '' : '（已停用）');
      selEl.append(opt);
    }
    selEl.value = activeId;
    selEl.onchange = () => setCdpActiveScene(selEl.value);
    // 渲染列表
    if (!scenes.length) {
      listEl.innerHTML = '<li class="empty">还没有自定义场景</li>';
      return;
    }
    listEl.innerHTML = '';
    for (const s of scenes) {
      const li = document.createElement('li');
      li.className = 'cdp-scene-item';
      li.dataset.id = s.id;
      const head = document.createElement('div');
      head.className = 'cdp-scene-head';
      const name = document.createElement('span');
      name.className = 'cdp-scene-name';
      name.textContent = s.name;
      const badge = document.createElement('span');
      badge.className = 'cdp-scene-badge';
      badge.textContent = s.id === activeId ? '● 当前' : (s.enabled ? '' : '已停用');
      if (s.id === activeId) badge.classList.add('active');
      head.append(name, badge);
      const desc = document.createElement('textarea');
      desc.className = 'cdp-scene-desc';
      desc.value = s.description || '';
      desc.rows = 3;
      desc.maxLength = 2000;
      const actions = document.createElement('div');
      actions.className = 'cdp-scene-actions';
      const toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.textContent = s.enabled ? '停用' : '启用';
      toggleBtn.onclick = () => updateCdpScene(s.id, { enabled: !s.enabled });
      const setActiveBtn = document.createElement('button');
      setActiveBtn.type = 'button';
      setActiveBtn.textContent = '设为当前';
      setActiveBtn.onclick = () => setCdpActiveScene(s.id);
      const saveDescBtn = document.createElement('button');
      saveDescBtn.type = 'button';
      saveDescBtn.textContent = '保存描述';
      saveDescBtn.onclick = () => updateCdpScene(s.id, { description: desc.value });
      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.textContent = '删除';
      delBtn.className = 'danger';
      delBtn.onclick = () => deleteCdpScene(s.id, s.name);
      actions.append(toggleBtn, setActiveBtn, saveDescBtn, delBtn);
      li.append(head, desc, actions);
      listEl.append(li);
    }
  } catch (e) {
    listEl.innerHTML = '<li class="empty">加载失败</li>';
  }
}

async function addCdpScene() {
  const nameEl = document.getElementById('cdp-scene-new-name');
  const descEl = document.getElementById('cdp-scene-new-desc');
  const name = nameEl.value.trim();
  const description = descEl.value.trim();
  if (!name) { nameEl.focus(); return; }
  try {
    const r = await fetch(`/api/characters/${currentDetailCharId}/scenes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description }),
    });
    if (!r.ok) throw new Error('add failed');
    nameEl.value = '';
    descEl.value = '';
    await loadCdpScenes();
  } catch (e) {
    alert('新增场景失败');
  }
}

async function updateCdpScene(sceneId, patch) {
  try {
    const r = await fetch(`/api/characters/${currentDetailCharId}/scenes/${sceneId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!r.ok) throw new Error('update failed');
    await loadCdpScenes();
  } catch (e) {
    alert('更新场景失败');
  }
}

async function deleteCdpScene(sceneId, sceneName) {
  if (!confirm(`确定删除场景「${sceneName}」吗？`)) return;
  try {
    const r = await fetch(`/api/characters/${currentDetailCharId}/scenes/${sceneId}`, { method: 'DELETE' });
    if (!r.ok) throw new Error('delete failed');
    await loadCdpScenes();
  } catch (e) {
    alert('删除场景失败');
  }
}

async function setCdpActiveScene(sceneId) {
  try {
    const r = await fetch(`/api/characters/${currentDetailCharId}/scenes/active`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scene_id: sceneId || null }),
    });
    if (!r.ok) throw new Error('set active failed');
    await loadCdpScenes();
  } catch (e) {
    alert('设置当前场景失败');
  }
}

// ---------- prompt rules (动态提示词库) ----------
async function loadCdpRules() {
  const listEl = document.getElementById('cdp-rule-list');
  if (!listEl || !currentDetailCharId) return;
  listEl.innerHTML = '<li class="empty">加载中…</li>';
  try {
    const r = await fetch(`/api/characters/${currentDetailCharId}/prompt-rules`);
    if (!r.ok) throw new Error('fetch failed');
    const data = await r.json();
    const rules = data.rules || [];
    if (!rules.length) {
      listEl.innerHTML = '<li class="empty">还没有提示词规则</li>';
      return;
    }
    listEl.innerHTML = '';
    for (const rule of rules) {
      const li = document.createElement('li');
      li.className = 'cdp-rule-item';
      li.dataset.id = rule.id;
      const conds = safeParseConds(rule.match_conditions);
      const condStr = formatConds(conds);
      li.innerHTML = `
        <div class="cdp-rule-head">
          <strong>${escHtml(rule.name)}</strong>
          <label class="cdp-rule-toggle"><input type="checkbox" ${rule.enabled ? 'checked' : ''} /> 启用</label>
          <button type="button" class="cdp-rule-del">删除</button>
        </div>
        <div class="cdp-rule-cond">匹配: ${condStr}</div>
        <details class="cdp-rule-edit">
          <summary>编辑内容与条件</summary>
          <textarea class="cdp-rule-edit-content" rows="3" maxlength="4000">${escHtml(rule.content)}</textarea>
          <div class="cdp-rule-cond-grid">
            <label>时间起 (HH:MM)<input type="text" class="cdp-rule-edit-ts" value="${escAttr(conds.time_start || '')}" /></label>
            <label>时间止 (HH:MM)<input type="text" class="cdp-rule-edit-te" value="${escAttr(conds.time_end || '')}" /></label>
            <label>情绪 (逗号分隔)<input type="text" class="cdp-rule-edit-emo" value="${escAttr((conds.emotions || []).join(', '))}" /></label>
            <label>关系 (逗号分隔)<input type="text" class="cdp-rule-edit-rel" value="${escAttr((conds.relationships || []).join(', '))}" /></label>
            <label>亲密度下限<input type="number" class="cdp-rule-edit-imin" min="0" max="100" value="${conds.intimacy_min ?? ''}" /></label>
            <label>亲密度上限<input type="number" class="cdp-rule-edit-imax" min="0" max="100" value="${conds.intimacy_max ?? ''}" /></label>
            <label data-span="2">关键词 (逗号分隔)<input type="text" class="cdp-rule-edit-kw" value="${escAttr((conds.keywords || []).join(', '))}" /></label>
          </div>
          <button type="button" class="cdp-rule-save primary">保存修改</button>
        </details>
      `;
      li.querySelector('.cdp-rule-toggle input').addEventListener('change', async (e) => {
        await fetch(`/api/characters/${currentDetailCharId}/prompt-rules/${rule.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: e.target.checked }),
        });
      });
      li.querySelector('.cdp-rule-del').addEventListener('click', async () => {
        if (!confirm('删除这条规则？')) return;
        const r = await fetch(`/api/characters/${currentDetailCharId}/prompt-rules/${rule.id}`, { method: 'DELETE' });
        if (r.ok) await loadCdpRules();
      });
      li.querySelector('.cdp-rule-save').addEventListener('click', async () => {
        const content = li.querySelector('.cdp-rule-edit-content').value.trim();
        const mc = buildMatchConditions(
          li.querySelector('.cdp-rule-edit-ts').value,
          li.querySelector('.cdp-rule-edit-te').value,
          li.querySelector('.cdp-rule-edit-emo').value,
          li.querySelector('.cdp-rule-edit-rel').value,
          li.querySelector('.cdp-rule-edit-imin').value,
          li.querySelector('.cdp-rule-edit-imax').value,
          li.querySelector('.cdp-rule-edit-kw').value,
        );
        const r = await fetch(`/api/characters/${currentDetailCharId}/prompt-rules/${rule.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content, match_conditions: mc }),
        });
        if (r.ok) { toast('已保存', 'success'); await loadCdpRules(); }
        else toast('保存失败', 'error');
      });
      listEl.appendChild(li);
    }
  } catch (e) {
    listEl.innerHTML = '<li class="empty">加载失败</li>';
  }
}

function safeParseConds(s) {
  try { return JSON.parse(s || '{}') || {}; } catch (_) { return {}; }
}

function buildMatchConditions(ts, te, emo, rel, imin, imax, kw) {
  const c = {};
  if (ts.trim()) c.time_start = ts.trim();
  if (te.trim()) c.time_end = te.trim();
  const emotions = emo.split(',').map(s => s.trim()).filter(Boolean);
  if (emotions.length) c.emotions = emotions;
  const relationships = rel.split(',').map(s => s.trim()).filter(Boolean);
  if (relationships.length) c.relationships = relationships;
  if (imin !== '' && imin != null) c.intimacy_min = parseInt(imin);
  if (imax !== '' && imax != null) c.intimacy_max = parseInt(imax);
  const keywords = kw.split(',').map(s => s.trim()).filter(Boolean);
  if (keywords.length) c.keywords = keywords;
  return JSON.stringify(c);
}

function formatConds(c) {
  const parts = [];
  if (c.time_start || c.time_end) parts.push(`时间 ${c.time_start || '?'}~${c.time_end || '?'}`);
  if (c.emotions?.length) parts.push(`情绪=${c.emotions.join('/')}`);
  if (c.relationships?.length) parts.push(`关系=${c.relationships.join('/')}`);
  if (c.intimacy_min != null || c.intimacy_max != null) parts.push(`亲密度 ${c.intimacy_min ?? 0}~${c.intimacy_max ?? 100}`);
  if (c.keywords?.length) parts.push(`关键词=${c.keywords.join('/')}`);
  return parts.length ? parts.join('，') : '无条件（始终注入）';
}

function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function escAttr(s) { return escHtml(s).replace(/"/g,'&quot;'); }

// ---------- chat history viewer (in character detail panel) ----------
let cdpHistPage = 0;
let cdpHistTotal = 0;
let cdpHistSelected = new Set();
const CDP_HIST_PAGE_SIZE = 30;

async function loadCdpHistory() {
  if (!currentDetailCharId) return;
  cdpHistPage = 0;
  cdpHistSelected.clear();
  await fetchCdpHistoryPage();
}

async function fetchCdpHistoryPage() {
  const listEl = document.getElementById('cdp-history-list');
  const pageInfo = document.getElementById('cdp-hist-page-info');
  const prevBtn = document.getElementById('cdp-hist-prev');
  const nextBtn = document.getElementById('cdp-hist-next');
  const delSelBtn = document.getElementById('cdp-hist-delete-selected');
  const selCount = document.getElementById('cdp-hist-selected-count');
  if (!listEl || !currentDetailCharId) return;
  listEl.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const offset = cdpHistPage * CDP_HIST_PAGE_SIZE;
    const res = await fetch(`/api/chat/history?character_id=${currentDetailCharId}&limit=${CDP_HIST_PAGE_SIZE}&offset=${offset}`);
    if (!res.ok) throw new Error('fetch failed');
    const { messages, total } = await res.json();
    cdpHistTotal = total || 0;
    listEl.innerHTML = '';
    if (!messages.length) {
      listEl.innerHTML = '<div class="empty">没有聊天记录</div>';
      pageInfo.textContent = '';
      prevBtn.disabled = true;
      nextBtn.disabled = true;
      return;
    }
    for (const m of messages) {
      const row = document.createElement('div');
      row.className = 'cdp-hist-row';
      row.dataset.id = m.id;
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'cdp-hist-check';
      cb.checked = cdpHistSelected.has(m.id);
      cb.addEventListener('change', () => {
        if (cb.checked) cdpHistSelected.add(m.id);
        else cdpHistSelected.delete(m.id);
        updateCdpHistSelectionUI();
      });
      const meta = document.createElement('div');
      meta.className = 'cdp-hist-meta';
      const roleTag = document.createElement('span');
      roleTag.className = 'cdp-hist-role ' + m.role;
      roleTag.textContent = m.role === 'user' ? '你' : '她';
      const time = document.createElement('span');
      time.className = 'cdp-hist-time';
      time.textContent = m.created_at || '';
      meta.append(roleTag, time);
      const content = document.createElement('div');
      content.className = 'cdp-hist-content';
      content.textContent = m.content;
      if (m.excluded_from_context) {
        const tag = document.createElement('span');
        tag.className = 'cdp-hist-excluded';
        tag.textContent = '已排除';
        content.appendChild(tag);
      }
      const delBtn = document.createElement('button');
      delBtn.className = 'cdp-hist-del';
      delBtn.textContent = '删除';
      delBtn.addEventListener('click', async () => {
        if (!confirm('确定删除这条消息？')) return;
        try {
          const r = await fetch(`/api/chat/messages/${m.id}?character_id=${currentDetailCharId}`, { method: 'DELETE' });
          if (!r.ok) { toast('删除失败', 'error'); return; }
          cdpHistSelected.delete(m.id);
          cdpHistTotal = Math.max(0, cdpHistTotal - 1);
          await fetchCdpHistoryPage();
          toast('已删除', 'success');
        } catch (e) { toast('删除失败', 'error'); }
      });
      row.append(cb, meta, content, delBtn);
      listEl.appendChild(row);
    }
    const totalPages = Math.ceil(cdpHistTotal / CDP_HIST_PAGE_SIZE);
    pageInfo.textContent = `第 ${cdpHistPage + 1} / ${totalPages} 页 · 共 ${cdpHistTotal} 条`;
    prevBtn.disabled = cdpHistPage === 0;
    nextBtn.disabled = cdpHistPage >= totalPages - 1;
    updateCdpHistSelectionUI();
  } catch (e) {
    listEl.innerHTML = '<div class="empty">加载失败</div>';
  }
}

function updateCdpHistSelectionUI() {
  const delBtn = document.getElementById('cdp-hist-delete-selected');
  const selCount = document.getElementById('cdp-hist-selected-count');
  if (!delBtn || !selCount) return;
  const n = cdpHistSelected.size;
  delBtn.disabled = n === 0;
  selCount.textContent = n > 0 ? `已选 ${n} 条` : '';
}

document.addEventListener('DOMContentLoaded', () => {
  const prevBtn = document.getElementById('cdp-hist-prev');
  const nextBtn = document.getElementById('cdp-hist-next');
  const selAllBtn = document.getElementById('cdp-hist-select-all');
  const delSelBtn = document.getElementById('cdp-hist-delete-selected');
  if (prevBtn) prevBtn.addEventListener('click', async () => {
    if (cdpHistPage > 0) { cdpHistPage--; await fetchCdpHistoryPage(); }
  });
  if (nextBtn) nextBtn.addEventListener('click', async () => {
    const totalPages = Math.ceil(cdpHistTotal / CDP_HIST_PAGE_SIZE);
    if (cdpHistPage < totalPages - 1) { cdpHistPage++; await fetchCdpHistoryPage(); }
  });
  if (selAllBtn) selAllBtn.addEventListener('click', () => {
    const checks = document.querySelectorAll('.cdp-hist-check');
    const allChecked = Array.from(checks).every(c => c.checked);
    checks.forEach(c => {
      c.checked = !allChecked;
      const id = parseInt(c.parentElement.dataset.id);
      if (c.checked) cdpHistSelected.add(id);
      else cdpHistSelected.delete(id);
    });
    updateCdpHistSelectionUI();
  });
  if (delSelBtn) delSelBtn.addEventListener('click', async () => {
    const ids = Array.from(cdpHistSelected);
    if (!ids.length || !confirm(`确定删除选中的 ${ids.length} 条消息？`)) return;
    try {
      const r = await fetch(`/api/chat/messages/batch-delete?character_id=${currentDetailCharId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      });
      if (!r.ok) { toast('删除失败', 'error'); return; }
      const data = await r.json();
      cdpHistSelected.clear();
      cdpHistTotal = Math.max(0, cdpHistTotal - (data.deleted || 0));
      await fetchCdpHistoryPage();
      toast(`已删除 ${data.deleted} 条`, 'success');
    } catch (e) { toast('删除失败', 'error'); }
  });
  const sdpPrev = document.getElementById('sdp-hist-prev');
  const sdpNext = document.getElementById('sdp-hist-next');
  const sdpSelAll = document.getElementById('sdp-hist-select-all');
  const sdpDelSel = document.getElementById('sdp-hist-delete-selected');
  if (sdpPrev) sdpPrev.addEventListener('click', async () => {
    if (sdpHistPage > 0) { sdpHistPage--; await fetchSdpHistoryPage(); }
  });
  if (sdpNext) sdpNext.addEventListener('click', async () => {
    const totalPages = Math.ceil(sdpHistTotal / SDP_HIST_PAGE_SIZE);
    if (sdpHistPage < totalPages - 1) { sdpHistPage++; await fetchSdpHistoryPage(); }
  });
  if (sdpSelAll) sdpSelAll.addEventListener('click', () => {
    const checks = document.querySelectorAll('#sdp-history-list .cdp-hist-check');
    const allChecked = Array.from(checks).every(c => c.checked);
    checks.forEach(c => {
      c.checked = !allChecked;
      const id = parseInt(c.parentElement.dataset.id);
      if (c.checked) sdpHistSelected.add(id);
      else sdpHistSelected.delete(id);
    });
    updateSdpHistSelectionUI();
  });
  if (sdpDelSel) sdpDelSel.addEventListener('click', async () => {
    const ids = Array.from(sdpHistSelected);
    if (!ids.length || !confirm(`确定删除选中的 ${ids.length} 条消息？`)) return;
    try {
      const r = await fetch(`/api/chat/messages/batch-delete?character_id=${sdpHistStoryId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      });
      if (!r.ok) { toast('删除失败', 'error'); return; }
      const data = await r.json();
      sdpHistSelected.clear();
      sdpHistTotal = Math.max(0, sdpHistTotal - (data.deleted || 0));
      await fetchSdpHistoryPage();
      toast(`已删除 ${data.deleted} 条`, 'success');
    } catch (e) { toast('删除失败', 'error'); }
  });
});

async function loadCdpEvolution() {
  const el = document.getElementById('cdp-evolutions');
  if (!el || !currentDetailCharId) return;
  el.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const r = await fetch(`/api/characters/${currentDetailCharId}/evolution`);
    if (!r.ok) throw new Error('fetch failed');
    const data = await r.json();
    const evos = data.evolutions || [];
    if (!evos.length) {
      el.innerHTML = '<div class="empty">还没有性格变化，多聊几句试试</div>';
      return;
    }
    el.innerHTML = '';
    for (const e of evos) {
      const item = document.createElement('div');
      item.className = 'cdp-moment';
      const change = document.createElement('div');
      change.className = 'cdp-moment-text';
      change.textContent = e.change;
      const time = document.createElement('div');
      time.className = 'cdp-moment-time';
      time.textContent = e.created_at;
      item.append(change, time);
      el.append(item);
    }
  } catch (e) {
    el.innerHTML = '<div class="empty">加载失败</div>';
  }
}

async function loadCdpRuntime() {
  if (!currentDetailCharId) return;
  try {
    const r = await fetch(`/api/characters/${currentDetailCharId}/runtime-state`);
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();
    const intimEl = document.getElementById('rt-intimacy');
    const msgEl = document.getElementById('rt-msg-count');
    if (intimEl) intimEl.textContent = `${d.intimacy ?? 0}/100`;
    if (msgEl) msgEl.textContent = String(d.user_message_count ?? 0);
    const mood = d.mood || {};
    const mk = ['happy','miss','jealous','annoyed','excited','bored'];
    const descEl = document.getElementById('rt-mood-desc');
    if (descEl) descEl.value = mood.description || '';
    for (const k of mk) {
      const el = document.getElementById('rt-mood-' + k);
      if (el) el.value = mood[k] ?? 0;
    }
    const sp = d.spatial || {};
    const spEl = document.getElementById('rt-spatial-desc');
    if (spEl) spEl.value = sp.description || '';
  } catch (e) {
    toast('加载运行状态失败', 'error');
  }
}

const _rtMoodSave = document.getElementById('rt-mood-save');
if (_rtMoodSave) {
  _rtMoodSave.addEventListener('click', async () => {
    if (!currentDetailCharId) return;
    const body = {
      mood: {
        happy: parseInt(document.getElementById('rt-mood-happy')?.value || '0'),
        miss: parseInt(document.getElementById('rt-mood-miss')?.value || '0'),
        jealous: parseInt(document.getElementById('rt-mood-jealous')?.value || '0'),
        annoyed: parseInt(document.getElementById('rt-mood-annoyed')?.value || '0'),
        excited: parseInt(document.getElementById('rt-mood-excited')?.value || '0'),
        bored: parseInt(document.getElementById('rt-mood-bored')?.value || '0'),
        description: document.getElementById('rt-mood-desc')?.value || '',
      },
    };
    const r = await fetch(`/api/characters/${currentDetailCharId}/runtime-state`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (r.ok) toast('心情已保存', 'success');
    else toast('保存失败', 'error');
  });
}

const _rtSpatialSave = document.getElementById('rt-spatial-save');
if (_rtSpatialSave) {
  _rtSpatialSave.addEventListener('click', async () => {
    if (!currentDetailCharId) return;
    const body = {
      spatial: {
        description: document.getElementById('rt-spatial-desc')?.value || '',
      },
    };
    const r = await fetch(`/api/characters/${currentDetailCharId}/runtime-state`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (r.ok) toast('时空状态已保存', 'success');
    else toast('保存失败', 'error');
  });
}

cdpTabBtns.forEach((t) => {
  t.addEventListener('click', () => switchCdpTab(t.dataset.cdpTab));
});

const cdpRelationshipSel = document.getElementById('cdp-relationship');
if (cdpRelationshipSel) {
  cdpRelationshipSel.addEventListener('change', async () => {
    if (!currentDetailCharId) return;
    try {
      await fetch(`/api/characters/${currentDetailCharId}/relationship`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ relationship: cdpRelationshipSel.value }),
      });
      toast('关系已更新', 'success', 1500);
    } catch (e) {
      toast('保存失败', 'error');
    }
  });
}

if (cdpBackBtn) {
  cdpBackBtn.addEventListener('click', () => exitContactsMode());
}

// Mobile: back button in the chat header returns to the conversation list.
if (btnBackList) {
  btnBackList.addEventListener('click', () => exitMobileChat());
}

if (cdpCancelBtn) {
  cdpCancelBtn.addEventListener('click', async () => {
    if (!currentDetailCharId) return;
    try {
      const r = await fetch(`/api/characters/${currentDetailCharId}`);
      if (r.ok) fillCdpForm(await r.json());
      cdpPendingAvatarFile = null;
      if (cdpPreviewUrl) { URL.revokeObjectURL(cdpPreviewUrl); cdpPreviewUrl = null; }
      if (cdpFormError) { cdpFormError.hidden = true; cdpFormError.textContent = ''; }
      toast('已撤销未保存的更改', 'success', 1500);
    } catch (e) {
      console.error('cancel reload failed', e);
      toast('重载失败,请检查网络', 'error');
    }
  });
}

if (cdpSaveBtn) {
  cdpSaveBtn.addEventListener('click', async () => {
    if (!currentDetailCharId || !cdpForm) return;
    if (cdpFormError) { cdpFormError.hidden = true; cdpFormError.textContent = ''; }

    const name = (cdpForm.elements.name.value || '').trim();
    if (!name) { showCdpError('名字不能为空'); return; }

    const payload = { name };
    for (const key of CDP_SCALAR_FIELDS) {
      if (key === 'name') continue;
      const el = cdpForm.elements[key];
      if (!el) continue;
      payload[key] = el.value || '';
    }
    for (const key of CDP_LIST_FIELDS) {
      const el = cdpForm.elements[key];
      if (!el) continue;
      payload[key] = el.value
        .split(/[\n,]/).map(s => s.trim()).filter(Boolean);
    }


    // circadian_overrides: collect 6 individual textareas into a dict
    const circKeys = ['late_night','early_morning','morning','noon','afternoon','evening'];
    const circObj = {};
    for (const k of circKeys) {
      const el = cdpForm.elements['circ_' + k];
      if (el && el.value.trim()) circObj[k] = el.value.trim();
    }
    payload.circadian_overrides = Object.keys(circObj).length ? circObj : null;

    // emotion_overrides: collect 4 groups (words + prompt) into a dict
    const emoKeys = ['sad','happy','angry','anxious'];
    const emoObj = {};
    for (const k of emoKeys) {
      const wEl = cdpForm.elements['emo_' + k + '_words'];
      const pEl = cdpForm.elements['emo_' + k + '_prompt'];
      const words = (wEl?.value || '').split(/[\n,]/).map(s => s.trim()).filter(Boolean);
      const prompt = (pEl?.value || '').trim();
      if (words.length || prompt) emoObj[k] = { words, prompt };
    }
    payload.emotion_overrides = Object.keys(emoObj).length ? emoObj : null;

    const fd = new FormData();
    fd.append('payload', JSON.stringify(payload));
    if (cdpPendingAvatarFile) fd.append('file', cdpPendingAvatarFile);

    cdpSaveBtn.disabled = true;
    const oldText = cdpSaveBtn.textContent;
    cdpSaveBtn.textContent = '保存中…';
    try {
      const res = await fetch(`/api/characters/${currentDetailCharId}`, {
        method: 'PATCH',
        body: fd,
      });
      if (!res.ok) {
        let msg = '保存失败,请稍后重试';
        try {
          const detail = await res.json();
          if (detail && detail.detail) msg = `保存失败:${detail.detail}`;
        } catch (_) {}
        showCdpError(msg);
        return;
      }
      const updated = await res.json();
      if (cdpPreviewUrl) { URL.revokeObjectURL(cdpPreviewUrl); cdpPreviewUrl = null; }
      cdpPendingAvatarFile = null;
      if (updated.card) characterCardCache.set(currentDetailCharId, updated.card);
      if (currentDetailCharId === activeCharId) {
        activeCharName = updated.name || activeCharName;
        activeCharAvatar = updated.avatar_path || activeCharAvatar;
        if (activeName) activeName.textContent = activeCharName;
        setActiveAvatar(activeCharAvatar, activeCharName);
      }
      const idx = allCharacters.findIndex(x => x.id === currentDetailCharId);
      if (idx >= 0) {
        allCharacters[idx] = { ...allCharacters[idx], name: updated.name || allCharacters[idx].name, avatar_path: updated.avatar_path || allCharacters[idx].avatar_path };
      }
      const cardEl = contactsGrid?.querySelector(`.contact-card[data-id="${currentDetailCharId}"]`);
      if (cardEl) {
        const nameEl = cardEl.querySelector('.cc-name-text');
        if (nameEl) nameEl.textContent = updated.name || nameEl.textContent;
        const avatarEl = cardEl.querySelector('.cc-avatar');
        if (avatarEl && updated.avatar_path) avatarEl.src = updated.avatar_path;
        const descEl = cardEl.querySelector('.cc-desc');
        if (descEl && updated.card) {
          const isStory = allCharacters[idx]?.card_type === 'story';
          const desc = isStory
            ? (updated.card.world_setting || updated.card.plot_summary || updated.card.opening || '').trim()
            : (updated.card.basic_info || updated.card.description || '').trim();
          descEl.textContent = desc ? desc.slice(0, 120) : (isStory ? '暂无故事简介' : '暂无简介');
        }
      }
      toast('已保存', 'success');
    } catch (err) {
      console.error('save panel failed', err);
      showCdpError('保存失败,请检查网络或服务状态');
    } finally {
      cdpSaveBtn.disabled = false;
      cdpSaveBtn.textContent = oldText;
    }
  });
}

async function cdpDeleteCurrent() {
  if (!currentDetailCharId) return;
  const c = allCharacters.find(x => x.id === currentDetailCharId);
  if (!c) return;
  if (!confirm(`删除角色「${c.name}」?该角色的对话记忆也会被清空。`)) return;
  try {
    const res = await fetch(`/api/characters/${currentDetailCharId}`, { method: 'DELETE' });
    if (!res.ok) {
      toast('删除失败,请稍后重试', 'error');
      return;
    }
    if (currentDetailCharId === activeCharId) {
      stopProactivePolling();
      activeCharId = null;
      activeCharName = '';
      activeCharAvatar = null;
      if (logEl) logEl.innerHTML = '';
      if (recallsEl) recallsEl.innerHTML = '<div class="empty">选择角色开始聊天后,她想起的事情会出现在这里。</div>';
    }
    closeCharDetail();
    await loadCharacters();
    refreshContactsIfVisible();
    if (allCharacters.length) openCharDetail(allCharacters[0].id);
    else exitContactsMode();
    toast('已删除', 'success');
  } catch (err) {
    console.error('delete failed', err);
    toast('删除失败,请检查网络', 'error');
  }
}

if (cdpDeleteBtn) cdpDeleteBtn.addEventListener('click', cdpDeleteCurrent);

if (cdpExportBtn) {
  cdpExportBtn.addEventListener('click', async () => {
    if (!currentDetailCharId) return;
    try {
      const res = await fetch(`/api/characters/${currentDetailCharId}/export`);
      if (!res.ok) { toast('导出失败', 'warn'); return; }
      const blob = await res.blob();
      const cd = res.headers.get('Content-Disposition') || '';
      let fname = 'export.json';
      const m = cd.match(/filename\*=UTF-8''(.+)/);
      if (m) fname = decodeURIComponent(m[1]);
      else {
        const m2 = cd.match(/filename="(.+?)"/);
        if (m2) fname = m2[1];
      }
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = fname;
      a.click();
      URL.revokeObjectURL(a.href);
      toast('已导出', 'success', 2000);
    } catch (err) {
      console.error('export failed', err);
      toast('导出失败,请检查网络', 'warn');
    }
  });
}

if (cdpChatBtn) {
  cdpChatBtn.addEventListener('click', async () => {
    const c = allCharacters.find(x => x.id === currentDetailCharId);
    if (!c) return;
    closeCharDetail();
    exitContactsMode();
    try { await selectCharacter(c.id, c.name, c.avatar_path, true); } catch (_) {}
  });
}

// ---------- character list ----------

async function loadCharacters() {
  const res = await fetch('/api/characters');
  if (!res.ok) {
    activeName.textContent = '— 加载失败 —';
    console.error('loadCharacters HTTP', res.status, await res.text());
    toast('加载角色失败,请刷新页面重试', 'error', 3500);
    return;
  }
  const { characters } = await res.json();
  allCharacters = characters;
  renderCharList(characters);
  if (!activeCharId && characters.length && !appShell?.classList.contains('contacts-mode')) {
    selectCharacter(characters[0].id, characters[0].name, characters[0].avatar_path);
  }
  if (!loadCharacters._storageChecked) {
    loadCharacters._storageChecked = true;
    try {
      const h = await fetch('/api/storage');
      if (h.ok) {
        const info = await h.json();
        const count = info.my_character_count || characters.length;
        toast(
          `已从本地恢复 ${count} 个角色 · 数据库 ${formatBytes(info.db_size_bytes)}`,
          'success',
          3000
        );
      }
    } catch (_) {}
  }
}

function formatBytes(n) {
  if (!n) return '0 B';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function renderCharList(characters) {
  charList.innerHTML = '';
  if (!characters.length) {
    activeName.textContent = '— 还没有角色,点 ＋ 导入 —';
    const li = document.createElement('li');
    li.className = 'empty';
    li.innerHTML = '点右上方 <span class="i-inline">＋</span> 导入 SillyTavern 角色卡 (.json / .png)';
    charList.appendChild(li);
    refreshIcons(li);
    return;
  }
  for (const c of characters) {
    const li = document.createElement('li');
    li.dataset.id = c.id;
    if (c.id === activeCharId) li.classList.add('active');

    const avatar = c.avatar_path || c.cover_url || makeGradientAvatar(c.name);
    const lastMsg = c.last_message;
    const isStory = c.card_type === 'story';
    const previewText = lastMsg
      ? `${lastMsg.role === 'user' ? '你' : (isStory ? '故事' : '她')}: ${lastMsg.content}`
      : (isStory ? '点击开始故事…' : '点击开始聊天…');
    const time = timeTag(lastMsg ? lastMsg.created_at : c.created_at);

    li.innerHTML = `
      <img class="avatar" src="${avatar}" alt="" />
      <div class="info">
        <div class="name"><span class="name-text"></span>${c.card_type === 'story' ? '<span class="story-tag">故事</span>' : '<span class="ai-tag">AI</span>'}</div>
        <div class="preview-line"></div>
      </div>
      <span class="time-tag"></span>
    `;
    li.querySelector('.name-text').textContent = c.name;
    li.querySelector('.preview-line').textContent = previewText;
    li.querySelector('.time-tag').textContent = time;
    li.addEventListener('click', () => {
      if (c.card_type === 'story') {
        selectStoryFromChat(c.id, c.name, c.avatar_path);
      } else {
        selectCharacter(c.id, c.name, c.avatar_path, true);
      }
    });
    charList.appendChild(li);
  }
  refreshIcons(charList);
}

if (searchInput) {
  searchInput.addEventListener('input', _debounce(() => {
    const q = searchInput.value.trim().toLowerCase();
    if (!q) { renderCharList(allCharacters); return; }
    renderCharList(allCharacters.filter((c) => c.name.toLowerCase().includes(q)));
  }, 200));
}

async function selectCharacter(id, name, avatarPath, fromUser = false) {
  closeCurrentEs();
  if (typeof closeMemEditModal === 'function') closeMemEditModal();
  activeCharId = id;
  activeCharName = name;
  activeCharAvatar = avatarPath || null;
  activeCharIsStory = false;
  appShell?.classList.remove('story-chat');
  closeChatDetailPanel();
  activeName.textContent = name;
  setActiveAvatar(activeCharAvatar, name);
  activeSub.textContent = '在线 · bge-m3 长期记忆已启用';
  for (const li of charList.querySelectorAll('li')) {
    li.classList.toggle('active', li.dataset.id === id);
  }
  if (fromUser) enterMobileChat();
  await fetch(`/api/characters/${id}/select`, { method: 'POST' });
  logEl.innerHTML = '';
  messageCount = 0;
  greetedThisSession = false;
  lastUserActivityTs = Date.now();
  // No auto-greet on selection — only the 5-minute idle nudge may fire later.
  clearTimeout(idleGreetTimer);
  updateSessionInfo();
  await loadHistory();
  startProactivePolling();
}

function setActiveAvatar(avatarPath, fallbackName) {
  if (avatarPath) {
    activeAvatar.src = avatarPath;
    activeAvatar.removeAttribute('hidden');
    if (activeAvatarFb) activeAvatarFb.setAttribute('hidden', '');
  } else {
    activeAvatar.setAttribute('hidden', '');
    if (activeAvatarFb) {
      activeAvatarFb.removeAttribute('hidden');
      activeAvatarFb.innerHTML = ic('ai');
    }
  }
}

let historyLoaded = 0;
let historyTotal = 0;
const HISTORY_PAGE = 100;

async function loadHistory() {
  if (!activeCharId) return;
  historyLoaded = 0;
  const res = await fetch(`/api/chat/history?character_id=${activeCharId}&limit=${HISTORY_PAGE}&offset=0`);
  if (!res.ok) return;
  const { messages, total } = await res.json();
  historyTotal = total || 0;
  historyLoaded = messages.length;
  if (historyTotal > historyLoaded) {
    insertLoadMoreButton(historyTotal - historyLoaded);
  }
  suppressScroll = true;
  for (const m of messages) {
    try {
      appendMessageRow(m.role, null, null, m.content, m.image_paths || [], { excluded: m.excluded_from_context, debug_ctx: m.debug_ctx });
    } catch (err) {
      console.error('history render failed for one row', err, m);
    }
  }
  suppressScroll = false;
  logEl.scrollTop = logEl.scrollHeight;
  messageCount = messages.length;
  updateSessionInfo();
}

function insertLoadMoreButton(remaining) {
  const existing = document.getElementById('load-more-btn');
  if (existing) existing.remove();
  const btn = document.createElement('button');
  btn.id = 'load-more-btn';
  btn.className = 'load-more-btn';
  btn.textContent = `加载更早的消息（还有 ${remaining} 条）`;
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = '加载中…';
    const prevHeight = logEl.scrollHeight;
    const prevScroll = logEl.scrollTop;
    const res = await fetch(`/api/chat/history?character_id=${activeCharId}&limit=${HISTORY_PAGE}&offset=${historyLoaded}`);
    if (!res.ok) { btn.disabled = false; btn.textContent = '加载失败，点击重试'; return; }
    const { messages, total } = await res.json();
    historyTotal = total || 0;
    historyLoaded += messages.length;
    btn.remove();
    suppressScroll = true;
    const firstChild = logEl.firstChild;
    for (const m of messages) {
      try {
        const row = appendMessageRow(m.role, null, null, m.content, m.image_paths || [], { excluded: m.excluded_from_context, debug_ctx: m.debug_ctx });
        logEl.insertBefore(row, firstChild);
      } catch (err) {
        console.error('history render failed for one row', err, m);
      }
    }
    suppressScroll = false;
    logEl.scrollTop = prevScroll + (logEl.scrollHeight - prevHeight);
    if (historyTotal > historyLoaded) {
      insertLoadMoreButton(historyTotal - historyLoaded);
    }
  });
  logEl.insertBefore(btn, logEl.firstChild);
}

// ---------- proactive greet ----------

function scheduleIdleGreet(immediate) {
  if (!activeCharId || greetedThisSession || activeCharIsStory) return;
  clearTimeout(idleGreetTimer);
  const delay = immediate ? 1200 : 5 * 60 * 1000;
  idleGreetTimer = setTimeout(triggerGreet, delay);
}

function noteUserActivity() {
  lastUserActivityTs = Date.now();
  if (greetedThisSession) return;
  clearTimeout(idleGreetTimer);
  idleGreetTimer = setTimeout(triggerGreet, 5 * 60 * 1000);
}

async function triggerGreet() {
  if (!activeCharId || greetedThisSession || activeCharIsStory) return;
  if (currentEs) return;
  greetedThisSession = true;

  const row = createMessageRow('bot', activeCharAvatar, activeCharName);
  const bubble = row.querySelector('.bubble');
  let buffer = '';

  currentAssistantRow = row;
  currentAssistantBubble = bubble;

  const es = new EventSource(`/api/chat/greet?character_id=${encodeURIComponent(activeCharId)}`);
  currentEs = es;

  es.addEventListener('greet_meta', (e) => {
    try { console.info('greet:', JSON.parse(e.data)); } catch (_) {}
  });
  es.addEventListener('token', (e) => {
    const { t } = e.data ? JSON.parse(e.data) : { t: '' };
    if (!buffer) {
      bubble.classList.remove('typing');
      bubble.innerHTML = '';
    }
    buffer += t;
    bubble.textContent = buffer;
    scrollToBottom();
  });
  es.addEventListener('warning', (e) => {
    try { console.warn('greet warning:', JSON.parse(e.data)); } catch (_) {}
  });
  es.addEventListener('done', () => {
    finalizeBubble(bubble, buffer);
    es.close();
    currentEs = null;
    currentAssistantBubble = null;
    currentAssistantRow = null;
    messageCount += 1;
    updateSessionInfo();
  });
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      if (!buffer) {
        bubble.classList.remove('typing');
        row.remove();
        toast('角色暂时无法主动问好', 'warn');
      } else {
        finalizeBubble(bubble, buffer);
      }
      currentEs = null;
    }
  };
}

// ---------- proactive message polling ----------
let proactiveTimer = null;
let lastProactiveCheck = 0;
const PROACTIVE_POLL_INTERVAL = 3 * 60 * 1000;

function startProactivePolling() {
  if (proactiveTimer) return;
  proactiveTimer = setInterval(checkProactive, PROACTIVE_POLL_INTERVAL);
}

function stopProactivePolling() {
  if (proactiveTimer) { clearInterval(proactiveTimer); proactiveTimer = null; }
}

function checkProactive() {
  if (!activeCharId || currentEs || activeCharIsStory) return;
  const now = Date.now();
  if (now - lastProactiveCheck < PROACTIVE_POLL_INTERVAL) return;
  lastProactiveCheck = now;

  let row = null;
  let bubble = null;
  let buffer = '';
  const es = new EventSource(`/api/chat/proactive?character_id=${encodeURIComponent(activeCharId)}`);

  es.addEventListener('token', (e) => {
    const { t } = e.data ? JSON.parse(e.data) : { t: '' };
    if (!buffer) {
      row = createMessageRow('bot', activeCharAvatar, activeCharName);
      bubble = row.querySelector('.bubble');
      bubble.classList.remove('typing');
      for (const node of Array.from(bubble.querySelectorAll(':scope > span.dot'))) {
        node.remove();
      }
      currentAssistantRow = row;
      currentAssistantBubble = bubble;
      currentEs = es;
    }
    buffer += t;
    bubble.textContent = buffer;
    scrollToBottom();
  });

  es.addEventListener('done', () => {
    if (buffer && bubble) {
      finalizeBubble(bubble, buffer);
      messageCount += 1;
      updateSessionInfo();
    }
    es.close();
    if (currentEs === es) {
      currentEs = null;
      currentAssistantBubble = null;
      currentAssistantRow = null;
    }
  });

  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      if (buffer && bubble) finalizeBubble(bubble, buffer);
      es.close();
      if (currentEs === es) {
        currentEs = null;
        currentAssistantBubble = null;
        currentAssistantRow = null;
      }
    }
  };
}

function updateSessionInfo() {
  if (!activeCharId) {
    sessionInfoEl.className = 'empty';
    sessionInfoEl.textContent = '尚未开始';
    return;
  }
  sessionInfoEl.className = 'session-info';
  sessionInfoEl.innerHTML = `<span>本轮已交换 <strong>${messageCount}</strong> 条消息 · 记忆由本地 bge-m3 检索</span>`;
}

// ---------- import ----------

importBtn.addEventListener('click', () => importInput.click());
importInput.addEventListener('change', async () => {
  const f = importInput.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  const res = await fetch('/api/characters/import', { method: 'POST', body: fd });
  importInput.value = '';
  if (!res.ok) {
    toast('导入失败,请检查文件格式是否正确', 'error', 3500);
    return;
  }
  const data = await res.json();
  const action = data.action === 'updated' ? '已更新' : '已导入';
  toast(`${action}「${data.name}」`, 'success');
  // confirm persistence by re-fetching immediately
  await loadCharacters();
  if (!allCharacters.find((c) => c.id === data.id)) {
    toast('⚠️ 角色入库后读取失败,请刷新页面', 'error', 5000);
    return;
  }
  selectCharacter(data.id, data.name, data.avatar_path);
});

// ---------- image upload (client side) ----------

imgInput.addEventListener('change', () => {
  noteUserActivity();
  pendingImages = [];
  preview.innerHTML = '';
  if (imgInput.files && imgInput.files.length) preview.removeAttribute('hidden');
  for (const file of imgInput.files) {
    const url = URL.createObjectURL(file);
    pendingImages.push({ file, url, name: file.name });
    const chip = document.createElement('div');
    chip.className = 'img-chip';
    chip.innerHTML = `<img src="${url}" alt=""><button class="x" title="移除"></button>`;
    chip.querySelector('.x').innerHTML = ic('x');
    chip.querySelector('.x').addEventListener('click', () => {
      const i = pendingImages.findIndex((p) => p.file === file);
      if (i >= 0) pendingImages.splice(i, 1);
      chip.remove();
      if (!pendingImages.length) preview.setAttribute('hidden', '');
    });
    preview.appendChild(chip);
  }
});

// ---------- composer ----------

msgEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    composer.requestSubmit();
  }
});
msgEl.addEventListener('input', noteUserActivity);

composer.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = msgEl.value.trim();
  if (!text && !pendingImages.length) return;
  if (!activeCharId) { toast('请先选择一个角色', 'warn'); return; }

  const imagePaths = [];
  for (const { file } of pendingImages) {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    if (r.ok) {
      const data = await r.json();
      imagePaths.push(data.url);
    }
  }
  pendingImages = [];
  preview.innerHTML = '';
  preview.setAttribute('hidden', '');

  currentUserRow = appendMessageRow('user', null, APP_UID, text, imagePaths);
  msgEl.value = '';
  autoresize();
  messageCount += 1;
  updateSessionInfo();
  if (!greetedThisSession) noteUserActivity();

  const row = createMessageRow('bot', activeCharAvatar, activeCharName);
  const assistantBubble = row.querySelector('.bubble');
  currentAssistantRow = row;
  currentAssistantBubble = assistantBubble;

  const params = new URLSearchParams({
    character_id: activeCharId,
    message: text,
  });
  for (const p of imagePaths) params.append('image_paths', p);
  const es = new EventSource(`/api/chat/send?${params.toString()}`);
  currentEs = es;

  let buffer = '';
  let displayReady = false;
  let debugCtx = null;
  function flushText() {
    const stripped = buffer.replace(/\[IMAGE:[^\]]*\]/g, '').trimStart();
    let textNode = Array.from(assistantBubble.childNodes).find((n) => n.nodeType === Node.TEXT_NODE);
    if (!textNode) {
      textNode = document.createTextNode('');
      assistantBubble.insertBefore(textNode, assistantBubble.firstChild);
    }
    textNode.data = flattenLineBreaks(stripped);
    scrollToBottom();
  }

  es.addEventListener('sleeping', (e) => {
    const { sleep_time, wake_time } = JSON.parse(e.data);
    if (currentAssistantRow) {
      currentAssistantRow.remove();
    }
    currentAssistantBubble = null;
    currentAssistantRow = null;
    toast(`她睡着了（${sleep_time}~${wake_time}），起床后会回复你`, 'warn', 5000);
  });
  es.addEventListener('retrieval', (e) => {
    const data = JSON.parse(e.data);
    renderRecalls(data.segments || []);
  });
  es.addEventListener('debug_ctx', (e) => {
    try { debugCtx = JSON.parse(e.data); console.log('[debug_ctx] received, keys:', Object.keys(debugCtx)); } catch (err) { console.error('[debug_ctx] parse error:', err); }
  });
  es.addEventListener('token', (e) => {
    const { t } = e.data ? JSON.parse(e.data) : { t: '' };
    if (!buffer) {
      // first token: simulate a "thinking" pause before text appears.
      // Late night (23:00-06:00) = longer pause — AI is sleepy.
      // Story mode skips this — narrative isn't tied to real-world time.
      const hr = new Date().getHours();
      const lateNight = !activeCharIsStory && (hr < 6 || hr >= 23);
      const delay = lateNight ? 1200 + Math.random() * 2000 : 400 + Math.random() * 1200;
      setTimeout(() => {
        displayReady = true;
        assistantBubble.classList.remove('typing');
        for (const node of Array.from(assistantBubble.querySelectorAll(':scope > span.dot'))) {
          node.remove();
        }
        flushText();
      }, delay);
    }
    buffer += t;
    if (displayReady) flushText();
  });
  es.addEventListener('image_ref', (e) => {
    const { url, caption } = JSON.parse(e.data);
    const img = document.createElement('img');
    img.src = url;
    img.alt = caption || '';
    img.title = caption || '';
    img.addEventListener('click', () => openLightbox(url));
    assistantBubble.appendChild(img);
    scrollToBottom();
  });
  es.addEventListener('warning', (e) => {
    try { console.warn('chat warning:', JSON.parse(e.data)); } catch (_) {}
  });
  es.addEventListener('skipped', (e) => {
    // Server told us the whole turn (user question + synthetic AI reply)
    // was dropped from the LLM history window and memU — usually because
    // the LLM timed out or errored. Both rows ARE persisted (so the user
    // can see the failed turn in the log) but flagged excluded_from_context
    // server-side. Here we just mark them visually; we do NOT remove them.
    const data = e.data ? JSON.parse(e.data) : {};
    if (currentAssistantRow) {
      currentAssistantRow.classList.add('skipped');
      const bubble = currentAssistantRow.querySelector('.bubble');
      if (bubble) {
        bubble.classList.add('skipped');
        const tag = document.createElement('div');
        tag.className = 'ctx-note';
        tag.textContent = '未送入对话上下文';
        bubble.appendChild(tag);
      }
    }
    if (currentUserRow) currentUserRow.classList.add('skipped');
    currentAssistantBubble = null;
    currentAssistantRow = null;
    currentUserRow = null;
    if (data.reason === 'llm_fallback') {
      toast('这次对话没能正常完成,已从后续上下文中丢弃(避免反复触发同样的问题)', 'warn', 4500);
    } else {
      toast('这次对话没能正常完成,已从后续上下文中丢弃', 'warn', 3500);
    }
  });
  es.addEventListener('milestone', (e) => {
    try {
      const ms = JSON.parse(e.data);
      toast(`新的重要记忆：${ms.title}`, 'info', 4000);
    } catch (_) {}
  });
  es.addEventListener('story_choices', (e) => {
    try {
      const data = JSON.parse(e.data);
      const choices = data.choices || [];
      if (choices.length && currentAssistantBubble) {
        renderStoryChoiceButtons(currentAssistantBubble, choices);
      }
    } catch (_) {}
  });
  es.addEventListener('error', (e) => {
    try {
      const data = e.data ? JSON.parse(e.data) : {};
      if (data.reason === 'context_exceeded') {
        toast(data.message || '模型上下文窗口不足', 'error', 6000);
      } else if (data.reason === 'auth_failed') {
        toast(data.message || 'API 认证失败', 'error', 6000);
      } else {
        toast('对话出错: ' + (data.message || '未知错误'), 'error', 5000);
      }
    } catch (_) {
      toast('对话出错', 'error', 4000);
    }
  });
  es.addEventListener('done', (e) => {
    // skipped turn already cleaned up its rows and shouldn't bump
    // message count. Guard the finalize path on the bubble still being
    // present (skipped sets it to null before done fires).
    if (currentAssistantBubble) {
      finalizeBubble(assistantBubble, buffer);
      let doneCtx = null;
      try { const d = e.data ? JSON.parse(e.data) : {}; doneCtx = d.debug_ctx || null; } catch (err) { console.error('[done] parse error:', err); }
      console.log('[done] debugCtx=', doneCtx, 'bubble=', assistantBubble);
      if (doneCtx) addDebugButton(assistantBubble, doneCtx);
      else if (debugCtx) addDebugButton(assistantBubble, debugCtx);
      messageCount += 1;
    }
    es.close();
    currentEs = null;
    currentAssistantBubble = null;
    currentAssistantRow = null;
    currentUserRow = null;
    updateSessionInfo();
  });

  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      es.close();
      if (!buffer) {
        assistantBubble.classList.remove('typing');
        assistantBubble.classList.add('error');
        assistantBubble.textContent = '抱歉,刚才的请求失败了,请重试';
        const ts = document.createElement('div');
        ts.className = 'timestamp';
        ts.textContent = nowStr();
        assistantBubble.appendChild(ts);
        toast('请求失败了,请检查网络后重试', 'error');
      } else {
        finalizeBubble(assistantBubble, buffer);
        toast('连接中断,已收到部分回复', 'warn');
      }
      currentEs = null;
    }
  };
});

// ---------- message row helpers ----------

function avatarFor(role, avatarOverride, name) {
  if (role === 'user') return avatarOverride || userAvatarDataUrl();
  return avatarOverride || makeGradientAvatar(name);
}

function avatarHTML(role, avatarSrc, name) {
  const src = avatarFor(role, avatarSrc, name);
  return `<img class="msg-avatar" src="${src}" alt="" />`;
}

function createMessageRow(role, avatarSrc, name) {
  const row = document.createElement('div');
  row.className = 'msg-row ' + role;
  const avatarHtml = avatarHTML(role, avatarSrc, name);
  const inner = `
    <div class="msg-stack">
      <span class="msg-name"></span>
      <div class="bubble ${role} typing"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
    </div>
  `;
  row.innerHTML = (role === 'user') ? inner + avatarHtml : avatarHtml + inner;
  row.querySelector('.msg-name').textContent = name || (role === 'user' ? '你' : '');
  logEl.appendChild(row);
  scrollToBottom();
  return row;
}

function appendMessageRow(role, avatarSrc, name, content, imagePaths, opts = {}) {
  const row = document.createElement('div');
  row.className = 'msg-row ' + role + (opts.excluded ? ' skipped' : '');
  const avatarHtml = avatarHTML(role, avatarSrc, name || activeCharName);
  const inner = `
    <div class="msg-stack">
      <span class="msg-name"></span>
      <div class="bubble ${role}${opts.excluded ? ' skipped' : ''}"></div>
    </div>
  `;
  row.innerHTML = (role === 'user') ? inner + avatarHtml : avatarHtml + inner;
  const bubble = row.querySelector('.bubble');
  bubble.textContent = flattenLineBreaks(content);
  for (const p of imagePaths) {
    const img = document.createElement('img');
    img.src = p;
    img.alt = '';
    img.addEventListener('click', () => openLightbox(p));
    bubble.appendChild(img);
  }
  if (opts.excluded) {
    const tag = document.createElement('div');
    tag.className = 'ctx-note';
    tag.textContent = '未送入对话上下文';
    bubble.appendChild(tag);
  }
  const ts = document.createElement('div');
  ts.className = 'timestamp';
  ts.textContent = nowStr();
  bubble.appendChild(ts);
  if (role === 'assistant' && content) addTtsButton(bubble);
  if (role === 'assistant' && opts.debug_ctx) {
    try { addDebugButton(bubble, JSON.parse(opts.debug_ctx)); } catch (_) {}
  }
  if (role === 'assistant' && content) addPoolMatchButton(bubble, content);
  if (role === 'assistant' && activeCharIsStory) renderStoryChoices(bubble);
  row.querySelector('.msg-name').textContent = name || (role === 'user' ? '你' : activeCharName);
  logEl.appendChild(row);
  scrollToBottom();
  return row;
}

function flattenLineBreaks(s) {
  return String(s == null ? '' : s)
    .replace(/\r\n/g, '\n')
    .replace(/\n+/g, (m) => (m.length >= 2 ? '\n\n' : ''));
}

function renderStoryChoices(bubble) {
  if (!activeCharIsStory) return;
  const textNode = Array.from(bubble.childNodes).find(n => n.nodeType === Node.TEXT_NODE);
  if (!textNode) return;
  const text = textNode.data;
  const choiceMatch = text.match(/【选择】([\s\S]*?)$/);
  if (!choiceMatch) return;
  const choicesText = choiceMatch[1].trim();
  const choices = [];
  const re = /(?:\d+|[A-Z])[.、)]\s*([\s\S]+?)(?=\s*(?:\d+|[A-Z])[.、)]\s|$)/g;
  let m;
  while ((m = re.exec(choicesText)) !== null) {
    choices.push(m[1].trim());
  }
  if (!choices.length) return;
  textNode.data = text.replace(/【选择】[\s\S]*?$/, '').trimEnd();
  renderStoryChoiceButtons(bubble, choices);
}

function renderStoryChoiceButtons(bubble, choices) {
  const existing = bubble.querySelector('.story-choices');
  if (existing) existing.remove();
  const choiceContainer = document.createElement('div');
  choiceContainer.className = 'story-choices';
  choices.forEach(choice => {
    const btn = document.createElement('button');
    btn.className = 'story-choice-btn';
    btn.textContent = choice;
    btn.addEventListener('click', () => {
      msgEl.value = choice;
      composer.requestSubmit();
      choiceContainer.remove();
    });
    choiceContainer.appendChild(btn);
  });
  const ts = bubble.querySelector('.timestamp');
  if (ts) bubble.insertBefore(choiceContainer, ts);
  else bubble.appendChild(choiceContainer);
}

function finalizeBubble(bubble, buffer) {
  bubble.classList.remove('typing');
  // Strip typing dots (.dot spans) but preserve any image_ref <img> elements
  // the picker already appended — otherwise they get wiped together with
  // the placeholder span.
  for (const node of Array.from(bubble.querySelectorAll(':scope > span.dot'))) {
    node.remove();
  }
  // If the bubble still has no text node yet (no tokens arrived, but image_ref
  // may already have populated <img> children), create one with the buffer.
  if (!Array.from(bubble.childNodes).some((n) => n.nodeType === Node.TEXT_NODE)) {
    bubble.appendChild(document.createTextNode(flattenLineBreaks(buffer)));
  }
  if (!bubble.querySelector('.timestamp')) {
    const ts = document.createElement('div');
    ts.className = 'timestamp';
    ts.textContent = nowStr();
    bubble.appendChild(ts);
  }
  if ((bubble.classList.contains('assistant') || bubble.classList.contains('bot')) && !bubble.querySelector('.tts-btn')) {
    addTtsButton(bubble);
  }
  if ((bubble.classList.contains('assistant') || bubble.classList.contains('bot')) && !bubble.querySelector('.pool-match-btn')) {
    const text = extractBubbleText(bubble);
    if (text) addPoolMatchButton(bubble, text);
  }
  if (activeCharIsStory) renderStoryChoices(bubble);
  scrollToBottom();
}

// ---------- debug context viewer ----------

const _debugSvg = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';

function addDebugButton(bubble, ctx) {
  if (bubble.querySelector('.debug-btn')) return;
  const ts = bubble.querySelector('.timestamp');
  if (!ts) { console.warn('[addDebugButton] no .timestamp found'); return; }
  const btn = document.createElement('button');
  btn.className = 'debug-btn';
  btn.title = '查看上下文';
  btn.type = 'button';
  btn.textContent = '🔍';
  btn.style.fontSize = '12px';
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    showDebugCtx(ctx);
  });
  ts.appendChild(btn);
  console.log('[addDebugButton] button added to', ts);
}

const _poolMatchSvg = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>';

function addPoolMatchButton(bubble, text) {
  if (bubble.querySelector('.pool-match-btn')) return;
  const ts = bubble.querySelector('.timestamp');
  if (!ts) return;
  const btn = document.createElement('button');
  btn.className = 'pool-match-btn';
  btn.title = '匹配照片';
  btn.type = 'button';
  btn.innerHTML = _poolMatchSvg;
  btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!activeCharId) return;
    btn.disabled = true;
    try {
      const res = await fetch(`/api/characters/${activeCharId}/pool/match`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (data.image) {
        openLightbox(data.image.url);
      } else {
        toast('照片池中没有匹配的图片', 'info', 2000);
      }
    } catch (err) {
      toast('匹配照片失败: ' + err.message, 'error', 3000);
    } finally {
      btn.disabled = false;
    }
  });
  ts.appendChild(btn);
}

function showDebugCtx(ctx) {
  const modal = document.getElementById('debug-ctx-modal');
  const body = document.getElementById('debug-ctx-body');
  if (!modal || !body) return;
  body.innerHTML = '';

  // System Prompt 全文 — 默认折叠（与下方分项重复，展开仅供调试）
  const sp = document.createElement('details');
  sp.className = 'dbg-full-prompt';
  sp.innerHTML = `<summary>System Prompt 全文（折叠，与下方分项重复）</summary>`;
  const spPre = document.createElement('pre');
  spPre.className = 'dbg-section-content mono';
  spPre.textContent = ctx.system_prompt || '(空)';
  sp.append(spPre);
  body.append(sp);

  // 各层分项 — 默认展开
  const sections = [];
  sections.push(['场景', ctx.scene_name || '—', '']);
  if (ctx.custom_scene && ctx.custom_scene.description) {
    sections.push(['场景描述', ctx.custom_scene.description, '']);
  }
  if (ctx.spatial_prompt) sections.push(['时空状态(生成时)', ctx.spatial_prompt, '']);
  if (ctx.mood_prompt) sections.push(['心情(生成时)', ctx.mood_prompt, '']);
  if (ctx.new_spatial) {
    sections.push(['时空状态(更新后)', `描述：${ctx.new_spatial.description || '(空)'}\n衣着：${ctx.new_spatial.outfit || '(空)'}`, 'dbg-new-state']);
  }
  if (ctx.new_mood) {
    sections.push(['心情(更新后)', JSON.stringify(ctx.new_mood, null, 2), 'dbg-new-state']);
  }
  if (ctx.events_prompt) sections.push(['事件追问', ctx.events_prompt, '']);
  if (ctx.intimacy_prompt) sections.push(['亲密度', ctx.intimacy_prompt, '']);
  if (ctx.relationship_prompt) sections.push(['关系类型', ctx.relationship_prompt, '']);
  if (ctx.lorebook_entries && ctx.lorebook_entries.length) {
    sections.push(['世界书', ctx.lorebook_entries.map(e => '• ' + e).join('\n'), '']);
  }
  if (ctx.recalled_segments && ctx.recalled_segments.length) {
    const segText = ctx.recalled_segments.map(s => `[${(s.score || 0).toFixed(3)}] ${s.text}`).join('\n---\n');
    sections.push(['记忆召回 (Recall)', segText, '']);
  }
  if (ctx.prompt_rules_prompt) sections.push(['提示词库(匹配)', ctx.prompt_rules_prompt, '']);
  if (ctx.skipped_layers && ctx.skipped_layers.length) {
    sections.push(['已跳过层(节约上下文)', ctx.skipped_layers.join('、'), '']);
  }
  sections.push(['历史轮数', String(ctx.history_turns || 0), '']);
  for (const [title, content, cls] of sections) {
    const h = document.createElement('h4');
    h.className = 'dbg-section-title';
    h.textContent = title;
    const pre = document.createElement('pre');
    pre.className = 'dbg-section-content' + (cls ? ' ' + cls : '');
    pre.textContent = content;
    body.append(h, pre);
  }
  // 最近 N 轮对话消息（注入到 messages 列表的 user/assistant 消息）
  if (ctx.history_messages && ctx.history_messages.length) {
    const h = document.createElement('h4');
    h.className = 'dbg-section-title';
    h.textContent = `最近对话 (${ctx.history_messages.length} 条)`;
    body.append(h);
    for (const msg of ctx.history_messages) {
      const pre = document.createElement('pre');
      pre.className = 'dbg-section-content dbg-msg-' + (msg.role || 'user');
      pre.textContent = `[${msg.role || '?'}] ${msg.content || ''}`;
      body.append(pre);
    }
  }
  modal.removeAttribute('hidden');
}

const _debugCloseBtn = document.getElementById('debug-ctx-close');
if (_debugCloseBtn) {
  _debugCloseBtn.addEventListener('click', () => {
    const m = document.getElementById('debug-ctx-modal');
    if (m) m.setAttribute('hidden', '');
  });
}
const _debugModal = document.getElementById('debug-ctx-modal');
if (_debugModal) {
  _debugModal.addEventListener('click', (e) => {
    if (e.target === _debugModal) _debugModal.setAttribute('hidden', '');
  });
}

// ---------- TTS (text-to-speech playback on AI bubbles) ----------

const _ttsSpeakerSvg = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>';
const _ttsPauseSvg = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';

function extractBubbleText(bubble) {
  const clone = bubble.cloneNode(true);
  clone.querySelectorAll('.timestamp, .tts-btn, .ctx-note, img').forEach(el => el.remove());
  return clone.textContent.trim();
}

function addTtsButton(bubble) {
  if (bubble.querySelector('.tts-btn')) return;
  const text = extractBubbleText(bubble);
  if (!text) return;
  const ts = bubble.querySelector('.timestamp');
  if (!ts) return;
  const btn = document.createElement('button');
  btn.className = 'tts-btn';
  btn.title = '播放语音';
  btn.type = 'button';
  btn.innerHTML = _ttsSpeakerSvg;
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    playTts(btn, text);
  });
  ts.appendChild(btn);
}

async function playTts(btn, text) {
  if (btn._audio) {
    if (btn._audio.paused) {
      btn._audio.play();
    } else {
      btn._audio.pause();
    }
    return;
  }
  btn.classList.add('loading');
  btn.disabled = true;
  try {
    const res = await fetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    btn._audio = audio;
    audio.addEventListener('play', () => { btn.classList.add('playing'); btn.innerHTML = _ttsPauseSvg; });
    audio.addEventListener('pause', () => { btn.classList.remove('playing'); btn.innerHTML = _ttsSpeakerSvg; });
    audio.addEventListener('ended', () => { btn.classList.remove('playing'); btn.innerHTML = _ttsSpeakerSvg; URL.revokeObjectURL(url); btn._audio = null; });
    audio.play();
  } catch (e) {
    console.error('tts failed', e);
    toast('语音合成失败', 'error');
  } finally {
    btn.classList.remove('loading');
    btn.disabled = false;
  }
}

function renderRecalls(segments) {
  if (!segments || !segments.length) {
    if (recallsEl) recallsEl.innerHTML = '<div class="empty">她现在还没想起什么～</div>';
    return;
  }
  if (!recallsEl) return;
  recallsEl.innerHTML = '';
  for (const seg of segments.slice(0, 5)) {
    const div = document.createElement('div');
    div.className = 'recall-item';
    const who = document.createElement('div');
    who.className = 'who';
    who.textContent = (seg.track === 'memory' ? '💭 相关度 ' : '📚 相关度 ') + (seg.score || 0).toFixed(2);
    const body = document.createElement('div');
    body.textContent = seg.text || '';
    div.appendChild(who);
    div.appendChild(body);
    recallsEl.appendChild(div);
  }
}

let suppressScroll = false;
let _scrollRafPending = false;
function scrollToBottom() {
  if (suppressScroll) return;
  if (_scrollRafPending) return;
  _scrollRafPending = true;
  requestAnimationFrame(() => {
    _scrollRafPending = false;
    logEl.scrollTop = logEl.scrollHeight;
  });
}

// ---------- clear chat (exclude history from context) ----------

if (btnClearChat) btnClearChat.addEventListener('click', async () => {
  if (!activeCharId) return;
  if (!confirm('清空当前对话上下文？\n\n清除后历史记录保留但不再进入 AI 上下文，角色设定将立即生效。')) return;
  try {
    const res = await fetch(`/api/chat/clear?character_id=${encodeURIComponent(activeCharId)}`, { method: 'POST' });
    if (!res.ok) {
      toast('清除失败，请稍后重试', 'warn');
      return;
    }
    const data = await res.json();
    logEl.innerHTML = '';
    if (recallsEl) recallsEl.innerHTML = '<div class="empty">她现在还没想起什么～</div>';
    toast(`已清空对话上下文（${data.cleared ?? 0} 条）`, 'success', 2500);
  } catch (err) {
    console.error('clear chat failed', err);
    toast('清除失败，请检查网络或服务状态', 'warn');
  }
});

// ---------- export current character (history + card) ----------

if (btnExportChat) {
  btnExportChat.addEventListener('click', async () => {
    if (!activeCharId) {
      toast('先选一个角色再导出', 'warn');
      return;
    }
    btnExportChat.disabled = true;
    try {
      const res = await fetch(`/api/characters/${activeCharId}/export`, { method: 'GET' });
      if (!res.ok) {
        toast(`导出失败 HTTP ${res.status}`, 'error', 3500);
        return;
      }
      // Use Content-Disposition filename if the server gave us one,
      // otherwise fall back to the character name we have in memory.
      const dispo = res.headers.get('Content-Disposition') || '';
      const m = dispo.match(/filename="([^"]+)"/);
      const filename = m ? m[1] : `ai-girlfriend-${activeCharName || 'character'}.json`;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      const msg = JSON.parse(await blob.text());
      const n = (msg.messages || []).length;
      toast(`已导出「${filename}」(${n} 条消息)`, 'success', 3000);
    } catch (e) {
      console.error(e);
      toast('导出出错:' + (e && e.message ? e.message : e), 'error', 4000);
    } finally {
      btnExportChat.disabled = false;
    }
  });
}

// ---------- toggle detail panel from chat header ----------

if (btnCharDetail) {
  btnCharDetail.addEventListener('click', () => {
    if (!activeCharId) return;
    const shell = appShell;
    if (!shell) return;
    const isDetailOpen = shell.classList.contains('detail-open');
    if (isDetailOpen) {
      closeChatDetailPanel();
    } else {
      openChatDetailPanel();
    }
  });
}

function openChatDetailPanel() {
  const shell = appShell;
  if (!shell || !activeCharId) return;
  shell.classList.add('detail-open');
  if (activeCharIsStory) {
    const sdp = $('#story-detail-panel');
    if (sdp) {
      sdp.removeAttribute('hidden');
      loadStoryDetail(activeCharId);
    }
    const cdp = $('#char-detail-panel');
    if (cdp) cdp.setAttribute('hidden', '');
  } else {
    const cdp = $('#char-detail-panel');
    if (cdp) {
      cdp.removeAttribute('hidden');
      openCharDetail(activeCharId, { fromChat: true });
    }
    const sdp = $('#story-detail-panel');
    if (sdp) sdp.setAttribute('hidden', '');
  }
}

function closeChatDetailPanel() {
  const shell = appShell;
  if (!shell) return;
  shell.classList.remove('detail-open');
  const cdp = $('#char-detail-panel');
  if (cdp) cdp.setAttribute('hidden', '');
  const sdp = $('#story-detail-panel');
  if (sdp) sdp.setAttribute('hidden', '');
}

// ---------- chat history import ----------

if (importChatBtn) {
  importChatBtn.addEventListener('click', () => importChatInput.click());
}
const btnImportChatHead = $('#btn-import-chat-head');
if (btnImportChatHead) {
  btnImportChatHead.addEventListener('click', () => importChatInput.click());
}
importChatInput.addEventListener('change', async () => {
  const f = importChatInput.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  importChatInput.value = '';
  if (activeCharId) {
    toast('正在导入聊天记录…');
    const res = await fetch(`/api/chat/import-messages?character_id=${encodeURIComponent(activeCharId)}`, { method: 'POST', body: fd });
    if (!res.ok) {
      toast('导入失败,请确认文件是有效的聊天导出格式', 'warn');
      return;
    }
    const data = await res.json();
    toast(`已导入 ${data.imported} 条消息`);
    await loadHistory();
    return;
  }
  fd.append('format', 'auto');
  chatImportSummary.textContent = '解析中…';
  chatImportList.innerHTML = '';
  chatImportModal.showModal();
  const res = await fetch('/api/import_chat', { method: 'POST', body: fd });
  if (!res.ok) {
    chatImportSummary.textContent = '解析失败,请确认文件是有效的聊天导出格式';
    return;
  }
  const data = await res.json();
  const reader = new FileReader();
  reader.onload = () => {
    chatImportState = {
      format: data.format_detected,
      segments: data.segments,
      fileBase64: (reader.result || '').split(',')[1],
    };
    renderChatImportList();
  };
  reader.readAsDataURL(f);
});

function renderChatImportList() {
  chatImportList.innerHTML = '';
  if (!chatImportState || !chatImportState.segments) {
    chatImportSummary.textContent = '未识别到内容';
    return;
  }
  chatImportSummary.textContent = `检测格式:${chatImportState.format} · ${chatImportState.segments.length} 段 · 选一段生成角色卡`;
  chatImportState.segments.forEach((seg, idx) => {
    const card = document.createElement('div');
    card.className = 'seg-card';

    const meta = document.createElement('div');
    meta.className = 'meta';
    const typeLabel = seg.chat_type === 'private' ? '私聊' : '群聊';
    const cand = seg.candidate || {};
    const range = (seg.first_timestamp || '').slice(0, 10) + ' ~ ' + (seg.last_timestamp || '').slice(0, 10);
    const candName = cand.name ? ` · 对方:${escapeHtml(cand.name)}` : '';
    const cnt = `${seg.text_message_count || seg.message_count || 0} 条`;
    meta.textContent = `${typeLabel} · ${seg.title || '(无标题)'} · ${cnt}${candName} · ${range}`;
    card.appendChild(meta);

    const sample = document.createElement('div');
    sample.className = 'sample';
    const firstFew = (cand.sample_messages || []).slice(0, 3).join(' / ');
    sample.textContent = firstFew
      ? firstFew.slice(0, 200)
      : (seg.chat_type !== 'private'
          ? '群聊目前不支持生成角色卡(只能选私聊)'
          : '这段没有候选对象,无法生成角色卡');
    card.appendChild(sample);

    const action = document.createElement('div');
    action.className = 'seg-card-action';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'primary';
    const canImport = seg.chat_type === 'private' && !!cand.name;
    btn.disabled = !canImport;
    btn.textContent = canImport ? '用此段生成角色卡' : '不可用';
    btn.addEventListener('click', () => openChatImportConfirm(idx));
    action.appendChild(btn);
    card.appendChild(action);

    chatImportList.appendChild(card);
  });
}

function escapeHtml(s) {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

async function openChatImportConfirm(segmentIndex) {
  if (!chatImportState) return;
  const seg = chatImportState.segments[segmentIndex];
  if (!seg || !seg.candidate) {
    toast('这段没有候选对象', 'warn');
    return;
  }
  const cand = seg.candidate;
  chatConfirmBody.innerHTML = '';
  const rows = [
    ['检测格式', chatImportState.format],
    ['聊天类型', seg.chat_type === 'private' ? '私聊' : '群聊'],
    ['段标题', seg.title || '(无标题)'],
    ['消息数', `${seg.text_message_count || seg.message_count} 条`],
    ['候选名', cand.name || '(未知)'],
    ['时间范围', `${(seg.first_timestamp || '').slice(0, 10)} ~ ${(seg.last_timestamp || '').slice(0, 10)}`],
  ];
  for (const [k, v] of rows) {
    const row = document.createElement('div');
    row.className = 'confirm-row';
    const kk = document.createElement('span');
    kk.className = 'k';
    kk.textContent = k;
    const vv = document.createElement('span');
    vv.className = 'v';
    vv.textContent = v;
    row.append(kk, vv);
    chatConfirmBody.appendChild(row);
  }

  const nameWrap = document.createElement('label');
  nameWrap.className = 'confirm-row';
  nameWrap.innerHTML = '<span class="k">角色名</span>';
  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.value = cand.name || '';
  nameInput.placeholder = '留空则用候选名';
  nameWrap.appendChild(nameInput);
  chatConfirmBody.appendChild(nameWrap);

  const note = document.createElement('p');
  note.className = 'confirm-note';
  note.textContent = '确认后会:1) 用 LLM 从对方话语里推一份 V2 角色卡;2) 把这段聊天按 30 条一组切成多条记忆写入 memU;3) 新建角色并切到它。原数据库里同名角色会被替换。';
  chatConfirmBody.appendChild(note);

  chatConfirmOk.onclick = async (ev) => {
    ev.preventDefault();
    await runChatImportConfirm(segmentIndex, nameInput.value.trim() || null);
  };

  chatConfirmModal.showModal();
}

async function runChatImportConfirm(segmentIndex, overrideName) {
  chatConfirmOk.disabled = true;
  chatConfirmOk.textContent = '生成中…(可能需要几秒)';
  try {
    const res = await fetch('/api/import_chat/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        segment_index: segmentIndex,
        character_name: overrideName,
        format: chatImportState.format,
        raw_b64: chatImportState.fileBase64,
      }),
    });
    if (!res.ok) {
      const txt = await res.text();
      toast('导入失败:' + txt.slice(0, 160), 'error', 4000);
      return;
    }
    const data = await res.json();
    chatConfirmModal.close();
    chatImportModal.close();
    toast(
      `已导入「${data.character_name}」· 写入 ${data.memory_files_committed} 段记忆 (${data.recall_segments_written} 条消息)`,
      'success',
      4000,
    );
    await loadCharacters();
    const created = allCharacters.find((c) => c.id === data.character_id);
    if (created) {
      selectCharacter(created.id, created.name, created.avatar_path);
    } else {
      activeCharId = data.character_id;
      await refreshChatMessages();
    }
  } catch (e) {
    console.error(e);
    toast('导入出错:' + (e && e.message ? e.message : e), 'error', 4000);
  } finally {
    chatConfirmOk.disabled = false;
    chatConfirmOk.textContent = '确认导入';
  }
}
// ---------- image pool (role self-photos) ----------

const cdpPoolList       = $('#cdp-pool-list');
const cdpPoolFileInput  = $('#cdp-pool-file');
const cdpPoolAddBtn     = $('#cdp-pool-add-btn');
const cdpPoolUploadBtn  = $('#cdp-pool-upload-btn');
const cdpPoolClearBtn   = $('#cdp-pool-clear-btn');
const cdpPoolDeleteAllBtn = $('#cdp-pool-delete-all-btn');
const cdpPoolQueue      = $('#cdp-pool-queue');
const cdpPoolQueueCount = $('#cdp-pool-queue-count');
const cdpPoolStatus     = $('#cdp-pool-upload-status');
// Local staging queue: files added via the file picker but not yet uploaded.
// Stored as an array of File objects so the same queue is usable across
// multiple add operations (user picks 3, clicks again, picks 2 more).
let cdpPoolQueueFiles = [];
let cdpPoolUploadedKeys = new Set();
let cdpPoolUploadingKeys = new Set();

// Caption generation runs server-side as a fire-and-forget background task.
// We poll the pool list briefly after upload so the freshly added row picks
// up the caption once it's ready.
const POOL_POLL_MS = 1500;
const POOL_POLL_MAX_TRIES = 8;

function renderCdpPool(images) {
  if (!cdpPoolList) return;
  cdpPoolList.innerHTML = '';
  if (!images.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = '还没有照片,先上传一张吧。';
    cdpPoolList.appendChild(li);
    return;
  }
  for (const img of images) {
    const li = document.createElement('li');
    li.className = 'cdp-pool-item';
    li.dataset.id = img.id;

    const wrap = document.createElement('div');
    wrap.className = 'cdp-pool-thumb-wrap';
    const pic = document.createElement('img');
    pic.src = img.thumb_url;
    pic.alt = img.caption || 'selfie';
    pic.loading = 'lazy';
    wrap.appendChild(pic);

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'cdp-pool-del';
    del.title = '删除';
    del.setAttribute('aria-label', '删除这张照片');
    del.textContent = '×';
    del.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      deleteCdpPoolItem(currentDetailCharId, img.id, li);
    });
    wrap.appendChild(del);

    const caption = document.createElement('div');
    caption.className = 'cdp-pool-caption';
    caption.dataset.id = img.id;
    const textSpan = document.createElement('span');
    textSpan.className = 'cdp-pool-caption-text';
    if (img.caption === '无法描述') {
      caption.classList.add('failed');
      caption.title = 'AI 没法描述这张照片。点「编辑描述」手动写一段场景描述,写完后这张图就有机会在对话里被挑中';
      textSpan.textContent = '无法描述';
    } else {
      caption.title = '点击文字或「编辑描述」按钮修改';
      textSpan.textContent = img.caption || '正在生成场景描述…';
    }
    caption.appendChild(textSpan);

    // Explicit edit entry — most visible on "无法描述" cards, but present on
    // every card so the affordance is discoverable.
    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'cdp-pool-caption-edit';
    editBtn.title = '编辑描述';
    editBtn.setAttribute('aria-label', '编辑描述');
    editBtn.textContent = '✎ 编辑描述';
    editBtn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      beginEditCdpCaption(caption, img.id);
    });
    caption.appendChild(editBtn);
    caption.addEventListener('click', () => {
      beginEditCdpCaption(caption, img.id);
    });

    li.appendChild(wrap);
    li.appendChild(caption);
    cdpPoolList.appendChild(li);
  }
}

async function loadCharPool(charId) {
  if (!cdpPoolList) return;
  cdpPoolList.innerHTML = '<li class="empty">加载中…</li>';
  try {
    const r = await fetch(`/api/characters/${charId}/pool`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    renderCdpPool(data.images || []);
  } catch (e) {
    console.error('loadCharPool failed', e);
    cdpPoolList.innerHTML = '<li class="empty">加载失败,稍后重试</li>';
  }
}

// Delete every pooled image of the character (all sources: manual upload,
// preset seed, AI-generated) with a confirmation gate.
async function deleteAllCharPool(charId) {
  const count = cdpPoolList ? cdpPoolList.querySelectorAll('li.cdp-pool-item').length : 0;
  if (!count) return;
  if (!confirm(`确定删除全部 ${count} 张照片吗？删除后不可恢复。`)) return;
  try {
    const r = await fetch(`/api/characters/${charId}/pool`, { method: 'DELETE' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    toast(`已删除 ${data.deleted} 张照片`, 'success');
    await loadCharPool(charId);
  } catch (e) {
    console.error('delete all pool failed', e);
    toast('删除失败:' + e.message, 'error');
  }
}

// Upload one pool image and optimistically prepend a thumbnail row whose
// caption gets filled in by the background caption job (polled below).
// Throws on HTTP error so the caller can isolate per-file failures.
async function uploadOneCdpPoolItem(charId, file) {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch(`/api/characters/${charId}/pool`, { method: 'POST', body: fd });
  if (!r.ok) {
    const errText = await r.text();
    throw new Error(`HTTP ${r.status}: ${errText.slice(0, 120)}`);
  }
  const data = await r.json();
  const items = cdpPoolList.querySelectorAll('li.empty');
  if (items.length) items.forEach((n) => n.remove());
  const li = document.createElement('li');
  li.className = 'cdp-pool-item';
  li.dataset.id = data.id;
  li.innerHTML =
    '<div class="cdp-pool-thumb-wrap"><img src="' + (data.thumb_url || data.url) +
    '" alt=""></div>' +
    '<div class="cdp-pool-caption"><span class="cdp-pool-caption-text">正在生成场景描述…</span>' +
    '<button type="button" class="cdp-pool-caption-edit" title="编辑描述" aria-label="编辑描述">✎ 编辑描述</button></div>';
  const cap = li.querySelector('.cdp-pool-caption');
  cap.addEventListener('click', () => beginEditCdpCaption(cap, data.id));
  li.querySelector('.cdp-pool-caption-edit').addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    beginEditCdpCaption(cap, data.id);
  });
  cdpPoolList.prepend(li);
  scheduleCdpPoolPoll(charId, data.id);
}

// Shared caption poller. Previously every uploaded image started its own
// loop that fetched the FULL pool list — uploading N images produced N
// concurrent list requests. Now we track the pool ids still awaiting a
// caption and poll once per tick for all of them.
let _cdpPoolPollTimer = null;
let _cdpPoolPollPending = new Set();

function applyCdpPoolCaption(poolId, target) {
  if (!target || !target.caption) return;  // still pending — leave the row as-is
  const li = cdpPoolList && cdpPoolList.querySelector(`li[data-id="${poolId}"]`);
  if (!li) return;
  const cap = li.querySelector('.cdp-pool-caption');
  // Don't clobber an in-progress edit.
  if (cap && !cap.classList.contains('editing')) {
    if (target.caption === '无法描述') {
      cap.classList.add('failed');
      cap.title = 'AI 没法描述这张照片。点「编辑描述」手动写一段场景描述,写完后这张图就有机会在对话里被挑中';
    } else {
      cap.classList.remove('failed');
      cap.title = '点击文字或「编辑描述」按钮修改';
    }
    const span = cap.querySelector('.cdp-pool-caption-text');
    if (span) span.textContent = target.caption;
    else cap.textContent = target.caption;
  }
  const img = li.querySelector('img');
  if (img && target.thumb_url) img.src = target.thumb_url;
}

function scheduleCdpPoolPoll(charId, poolId) {
  _cdpPoolPollPending.add(poolId);
  if (_cdpPoolPollTimer) return;
  let tries = 0;
  const tick = async () => {
    _cdpPoolPollTimer = null;
    if (!_cdpPoolPollPending.size) return;
    tries++;
    try {
      const r = await fetch(`/api/characters/${charId}/pool`);
      if (r.ok) {
        const data = await r.json();
        const byId = new Map((data.images || []).map((x) => [x.id, x]));
        for (const pid of Array.from(_cdpPoolPollPending)) {
          const target = byId.get(pid);
          if (target && target.caption) {
            applyCdpPoolCaption(pid, target);
            _cdpPoolPollPending.delete(pid);
          }
        }
      }
    } catch (_) { /* keep polling */ }
    if (_cdpPoolPollPending.size && tries < POOL_POLL_MAX_TRIES) {
      _cdpPoolPollTimer = setTimeout(tick, POOL_POLL_MS);
    } else if (_cdpPoolPollPending.size) {
      if (cdpPoolStatus) cdpPoolStatus.textContent = '描述生成较慢,稍后会自动出现';
      _cdpPoolPollPending.clear();
    } else if (cdpPoolStatus) {
      cdpPoolStatus.textContent = '';
    }
  };
  _cdpPoolPollTimer = setTimeout(tick, POOL_POLL_MS);
}

function stopCdpPoolPoll() {
  if (_cdpPoolPollTimer) { clearTimeout(_cdpPoolPollTimer); _cdpPoolPollTimer = null; }
  _cdpPoolPollPending.clear();
}

function beginEditCdpCaption(capEl, poolId) {
  if (capEl.classList.contains('editing')) return;
  const textSpan = capEl.querySelector('.cdp-pool-caption-text');
  const editBtn = capEl.querySelector('.cdp-pool-caption-edit');
  const original = textSpan ? textSpan.textContent : (capEl.dataset.original || '');
  // Placeholder seed: empty for not-yet-generated captions, otherwise
  // prefill the textarea with the current caption so the user can edit
  // it in place (including the "无法描述" failure label).
  const seed = (original === '正在生成场景描述…') ? '' : original;
  capEl.classList.add('editing');
  capEl.title = '回车保存,Shift+回车换行,Esc 取消';
  if (textSpan) textSpan.hidden = true;
  if (editBtn) editBtn.hidden = true;

  const ta = document.createElement('textarea');
  ta.value = seed;
  ta.rows = 2;
  ta.maxLength = 200;
  ta.placeholder = '给这张照片写一句场景描述(影响 AI 何时挑它)';
  capEl.appendChild(ta);
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);

  const setText = (t) => {
    if (textSpan) textSpan.textContent = t;
    else capEl.textContent = t;
    if (editBtn) editBtn.hidden = false;
  };
  let done = false;
  const cancel = () => {
    if (done) return;
    done = true;
    ta.remove();
    capEl.classList.remove('editing');
    capEl.title = '';
    setText(original);
  };
  const commit = async () => {
    if (done) return;
    done = true;
    const newText = ta.value.trim();
    ta.remove();
    capEl.classList.remove('editing');
    capEl.title = '';
    setText(newText || '(空)');
    if (newText === original.trim()) {
      // No change.
      return;
    }
    try {
      const r = await fetch(
        `/api/characters/${currentDetailCharId}/pool/${poolId}/caption`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ caption: newText }),
        }
      );
      if (!r.ok) {
        const errText = await r.text();
        throw new Error(`HTTP ${r.status}: ${errText.slice(0, 120)}`);
      }
      const data = await r.json();
      if (data.caption === '无法描述') {
        capEl.classList.add('failed');
        capEl.title = 'AI 没法描述这张照片。点「编辑描述」手动写一段场景描述,写完后这张图就有机会在对话里被挑中';
      } else {
        capEl.classList.remove('failed');
        capEl.title = '点击文字或「编辑描述」按钮修改';
      }
      setText(data.caption || '(空)');
      if (cdpPoolStatus) {
        cdpPoolStatus.textContent = data.embedding_updated
          ? '已保存,检索排序已更新'
          : '已保存';
      }
    } catch (e) {
      console.error('edit caption failed', e);
      if (cdpPoolStatus) cdpPoolStatus.textContent = '保存失败:' + e.message;
      setText(original); // rollback display
    }
  };

  ta.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      commit();
    } else if (ev.key === 'Escape') {
      ev.preventDefault();
      cancel();
    }
  });
  ta.addEventListener('blur', () => {
    // Commit if the user just tabbed away; cancel if already resolved.
    if (!done) commit();
  });
}

async function deleteCdpPoolItem(charId, poolId, li) {
  if (!confirm('确定删除这张照片?之后她不会再在对话里发出来。')) return;
  try {
    const r = await fetch(`/api/characters/${charId}/pool/${poolId}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    li.remove();
    if (!cdpPoolList.querySelector('li.cdp-pool-item')) {
      const empty = document.createElement('li');
      empty.className = 'empty';
      empty.textContent = '还没有照片,先上传一张吧。';
      cdpPoolList.appendChild(empty);
    }
  } catch (e) {
    console.error('delete pool item failed', e);
    toast('删除失败:' + e.message, 'error');
  }
}

// ---------- pool upload: staged queue ----------

function renderCdpPoolQueue() {
  if (!cdpPoolQueue || !cdpPoolQueueCount) return;
  cdpPoolQueueCount.textContent = String(cdpPoolQueueFiles.length);
  const hasItems = cdpPoolQueueFiles.length > 0;
  cdpPoolQueue.hidden = !hasItems;
  if (cdpPoolUploadBtn) cdpPoolUploadBtn.disabled = !hasItems;
  if (cdpPoolClearBtn) cdpPoolClearBtn.disabled = !hasItems;
  cdpPoolQueue.innerHTML = '';
  if (!hasItems) return;
  cdpPoolQueueFiles.forEach((file, idx) => {
    const li = document.createElement('li');
    li.className = 'cdp-pool-item cdp-pool-queue-item';
    const url = URL.createObjectURL(file);
    li.innerHTML =
      '<div class="cdp-pool-thumb-wrap"><img src="' + url + '" alt=""></div>' +
      '<div class="cdp-pool-caption"><span class="cdp-pool-caption-text">' +
      escapeHtml(file.name) + ' · ' + (file.size / 1024).toFixed(1) + ' KB</span></div>' +
      '<button type="button" class="cdp-pool-del" title="从队列移除">✕</button>';
    // Revoke the object URL when the queue entry leaves the DOM.
    li.querySelector('img').addEventListener('load', () => URL.revokeObjectURL(url), { once: true });
    li.querySelector('img').addEventListener('error', () => URL.revokeObjectURL(url), { once: true });
    li.querySelector('.cdp-pool-del').addEventListener('click', (ev) => {
      ev.preventDefault();
      cdpPoolQueueFiles.splice(idx, 1);
      renderCdpPoolQueue();
    });
    cdpPoolQueue.appendChild(li);
  });
}

function escapeHtml(s) {
  return String(s).replace(/[<>&"']/g, (c) => ({
    '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function _fileKey(file) {
  return file.name + '|' + file.size + '|' + file.lastModified;
}

function _debounce(fn, ms) {
  let t = null;
  return function(...args) {
    clearTimeout(t);
    t = setTimeout(() => fn.apply(this, args), ms);
  };
}

function _dedupeFiles(newFiles, queueFiles, uploadedKeys, uploadingKeys) {
  const existing = new Set();
  for (const f of queueFiles) existing.add(_fileKey(f));
  if (uploadedKeys) for (const k of uploadedKeys) existing.add(k);
  if (uploadingKeys) for (const k of uploadingKeys) existing.add(k);
  const accepted = [];
  let dupCount = 0;
  for (const f of newFiles) {
    const key = _fileKey(f);
    if (existing.has(key)) { dupCount++; continue; }
    existing.add(key);
    accepted.push(f);
  }
  return { accepted, dupCount };
}

// Open the native picker. The OS dialog supports multi-select (Ctrl+A,
// Ctrl+click, Shift+click on Windows; Cmd+click on macOS) when the
// underlying <input type="file" multiple> advertises it.
if (cdpPoolAddBtn && cdpPoolFileInput) {
  cdpPoolAddBtn.addEventListener('click', () => {
    // Reset so picking the same file twice still fires `change`.
    cdpPoolFileInput.value = '';
    cdpPoolFileInput.click();
  });
  cdpPoolFileInput.addEventListener('change', () => {
    const files = cdpPoolFileInput.files;
    if (!files || files.length === 0) return;
    const { accepted, dupCount } = _dedupeFiles(
      Array.from(files), cdpPoolQueueFiles, cdpPoolUploadedKeys, cdpPoolUploadingKeys
    );
    for (const f of accepted) cdpPoolQueueFiles.push(f);
    if (dupCount > 0) toast(`跳过 ${dupCount} 张重复图片`, 'info', 3000);
    renderCdpPoolQueue();
  });
}

if (cdpPoolClearBtn) {
  cdpPoolClearBtn.addEventListener('click', () => {
    cdpPoolQueueFiles = [];
    renderCdpPoolQueue();
    if (cdpPoolStatus) cdpPoolStatus.textContent = '';
  });
}

if (cdpPoolDeleteAllBtn) {
  cdpPoolDeleteAllBtn.addEventListener('click', () => {
    if (currentDetailCharId) deleteAllCharPool(currentDetailCharId);
  });
}

const cdpPoolRecaptionAllBtn = $('#cdp-pool-recaption-all-btn');
// Batch recaption is serial server-side, so it can take a while. Poll
// periodically but only patch caption text in place — never rebuild the
// whole grid (that caused image flicker and re-created every listener).
let _cdpRecaptionTimer = null;

function stopCdpRecaptionPoll() {
  if (_cdpRecaptionTimer) { clearInterval(_cdpRecaptionTimer); _cdpRecaptionTimer = null; }
}

async function refreshCdpPoolCaptionsOnly(charId) {
  try {
    const r = await fetch(`/api/characters/${charId}/pool`);
    if (!r.ok) return;
    const data = await r.json();
    for (const img of (data.images || [])) applyCdpPoolCaption(img.id, img);
  } catch (_) { /* keep polling */ }
}

if (cdpPoolRecaptionAllBtn) {
  cdpPoolRecaptionAllBtn.addEventListener('click', async () => {
    if (!currentDetailCharId) return;
    const charId = currentDetailCharId;
    cdpPoolRecaptionAllBtn.disabled = true;
    cdpPoolRecaptionAllBtn.textContent = '🔄 正在描述…';
    try {
      const res = await fetch(`/api/characters/${charId}/pool/recaption-all`, { method: 'POST' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (!data.queued) {
        toast('没有需要重新描述的图片', 'info', 3000);
        cdpPoolRecaptionAllBtn.disabled = false;
        cdpPoolRecaptionAllBtn.textContent = '🔄 重新描述';
        return;
      }
      toast(`正在重新描述 ${data.queued} 张图片…`, 'info', 3000);
      stopCdpRecaptionPoll();
      let polls = 0;
      const maxPolls = 60;  // 60 × 3s = 3min ceiling
      const tick = async () => {
        polls++;
        await refreshCdpPoolCaptionsOnly(charId);
        if (polls >= maxPolls) {
          stopCdpRecaptionPoll();
          cdpPoolRecaptionAllBtn.disabled = false;
          cdpPoolRecaptionAllBtn.textContent = '🔄 重新描述';
        }
      };
      _cdpRecaptionTimer = setInterval(tick, 3000);
      tick();
    } catch (e) {
      toast('重新描述失败: ' + e.message, 'error', 4000);
      cdpPoolRecaptionAllBtn.disabled = false;
      cdpPoolRecaptionAllBtn.textContent = '🔄 重新描述';
    }
  });
}

const cdpSceneAddBtn = $('#cdp-scene-add-btn');
if (cdpSceneAddBtn) {
  cdpSceneAddBtn.addEventListener('click', () => {
    if (currentDetailCharId) addCdpScene();
  });
}

const cdpRuleAddBtn = $('#cdp-rule-add-btn');
if (cdpRuleAddBtn) {
  cdpRuleAddBtn.addEventListener('click', async () => {
    if (!currentDetailCharId) return;
    const name = $('#cdp-rule-new-name').value.trim();
    const content = $('#cdp-rule-new-content').value.trim();
    if (!name || !content) { toast('名称和内容不能为空', 'error'); return; }
    const mc = buildMatchConditions(
      $('#cdp-rule-new-ts').value, $('#cdp-rule-new-te').value,
      $('#cdp-rule-new-emo').value, $('#cdp-rule-new-rel').value,
      $('#cdp-rule-new-imin').value, $('#cdp-rule-new-imax').value,
      $('#cdp-rule-new-kw').value,
    );
    const r = await fetch(`/api/characters/${currentDetailCharId}/prompt-rules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, content, match_conditions: mc }),
    });
    if (r.ok) {
      $('#cdp-rule-new-name').value = '';
      $('#cdp-rule-new-content').value = '';
      $('#cdp-rule-new-ts').value = '';
      $('#cdp-rule-new-te').value = '';
      $('#cdp-rule-new-emo').value = '';
      $('#cdp-rule-new-rel').value = '';
      $('#cdp-rule-new-imin').value = '';
      $('#cdp-rule-new-imax').value = '';
      $('#cdp-rule-new-kw').value = '';
      toast('规则已创建', 'success');
      await loadCdpRules();
    } else {
      toast('创建失败', 'error');
    }
  });
}

const cdpRulePreseedBtn = $('#cdp-rule-preseed-btn');
if (cdpRulePreseedBtn) {
  cdpRulePreseedBtn.addEventListener('click', async () => {
    if (!currentDetailCharId) return;
    if (!confirm('一键预置 6 条昼夜节律规则（深夜/清晨/上午/中午/下午/晚上）？')) return;
    const r = await fetch(`/api/characters/${currentDetailCharId}/prompt-rules/preseed-circadian`, { method: 'POST' });
    if (r.ok) {
      const data = await r.json();
      toast(`已创建 ${data.created} 条昼夜节律规则`, 'success');
      await loadCdpRules();
    } else {
      toast('预置失败', 'error');
    }
  });
}

if (cdpPoolUploadBtn) {
  cdpPoolUploadBtn.addEventListener('click', async () => {
    if (!currentDetailCharId) return;
    if (cdpPoolQueueFiles.length === 0) return;
    const files = cdpPoolQueueFiles.slice(); // snapshot, queue may be mutated
    cdpPoolUploadBtn.disabled = true;
    if (cdpPoolClearBtn) cdpPoolClearBtn.disabled = true;
    if (cdpPoolStatus) cdpPoolStatus.textContent = `上传中 0 / ${files.length}…`;

    // Successful uploads remove their entry from the queue; failures keep
    // theirs so the user can retry without re-picking.
    const uploadedNames = new Set();
    let done = 0;
    let failed = 0;
    // Bounded concurrency: uploading 50 images at once overwhelmed both
    // the browser connection pool and the backend. Process in small
    // batches instead of Promise.all-ing everything simultaneously.
    const UPLOAD_CONCURRENCY = 3;
    for (let i = 0; i < files.length; i += UPLOAD_CONCURRENCY) {
      const batch = files.slice(i, i + UPLOAD_CONCURRENCY);
      await Promise.all(batch.map(async (file) => {
        const key = _fileKey(file);
        cdpPoolUploadingKeys.add(key);
        try {
          await uploadOneCdpPoolItem(currentDetailCharId, file);
          uploadedNames.add(key);
          cdpPoolUploadedKeys.add(key);
        } catch (e) {
          failed += 1;
          console.error('upload pool item failed', file.name, e);
          toast(`「${file.name}」上传失败:${e.message || e}`, 'error', 4000);
        } finally {
          cdpPoolUploadingKeys.delete(key);
          done += 1;
          if (cdpPoolStatus) {
            cdpPoolStatus.textContent =
              failed > 0
                ? `已完成 ${done - failed} / ${files.length},失败 ${failed}`
                : `上传中 ${done} / ${files.length}…`;
          }
        }
      }));
    }

    // Trim uploaded entries (by name+size match, since File objects can't
    // be compared by identity after awaiting across the microtask queue).
    cdpPoolQueueFiles = cdpPoolQueueFiles.filter(
      (f) => !uploadedNames.has(_fileKey(f))
    );
    renderCdpPoolQueue();

    if (failed === 0) {
      if (cdpPoolStatus) cdpPoolStatus.textContent = `已上传 ${files.length} 张`;
    } else if (done - failed > 0) {
      if (cdpPoolStatus) cdpPoolStatus.textContent =
        `完成 ${done - failed} 张,失败 ${failed} 张(留队列里可重试)`;
    } else {
      if (cdpPoolStatus) cdpPoolStatus.textContent = `全部 ${failed} 张都失败了`;
    }
  });
}

// Render empty state on first paint so the queue placeholder is right.
renderCdpPoolQueue();

// ---------- bootstrap ----------
// Swap <span data-i="..."> placeholders in the static template with inline SVGs,
// then load characters + history from the server (refresh must restore the list).
function boot() {
  if (window.Icons) window.Icons.initIcons(document);
  initTheme();
  initStoryTemplates();
  loadCharacters();
  // Legacy deep link: /chat?view=contacts → migrate to the hash router.
  if (new URLSearchParams(location.search).get('view') === 'contacts') {
    history.replaceState(null, '', location.pathname + '#contacts');
  }
  // Restore the view from the URL hash (#contacts / #contacts/<id>) so
  // refresh, back/forward and shared links land on the same view.
  applyRoute();
}

// Sync <meta name="theme-color"> with the OS dark/light preference so the
// browser chrome (in-app browser address bar, iOS status bar tint) matches
// the active palette. CSS itself reads prefers-color-scheme directly via
// @media, so this only updates the static meta tag.
function initTheme() {
  const lightColor = '#FAF7F2';
  const darkColor  = '#1A1816';
  const meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) return;
  const apply = (isDark) => {
    meta.setAttribute('content', isDark ? darkColor : lightColor);
  };
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  apply(mq.matches);
  // Safari < 14 used addListener; modern Chromium/Firefox use addEventListener.
  if (mq.addEventListener) {
    mq.addEventListener('change', (e) => apply(e.matches));
  } else if (mq.addListener) {
    mq.addListener((e) => apply(e.matches));
  }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
// Clean up SSE connections and timers when the page is hidden or unloaded
// to prevent ghost writes and connection leaks.
window.addEventListener('pagehide', () => {
  closeCurrentEs();
  stopProactivePolling();
  if (typeof stopCdpPoolPoll === 'function') stopCdpPoolPoll();
  if (typeof stopCdpRecaptionPoll === 'function') stopCdpRecaptionPoll();
  if (typeof stopSdpPoolPoll === 'function') stopSdpPoolPoll();
  if (typeof stopSdpRecaptionPoll === 'function') stopSdpRecaptionPoll();
});
