const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');
const path = require('node:path');
const { seed } = require('./helpers/view-state-harness.cjs');
const { dom } = require('./helpers/synthetic-dom.cjs');

const source = readFileSync(path.join(__dirname, '..', 'chatui', 'app.js'), 'utf8');

function slice(startAnchor, endAnchor) {
  const start = source.indexOf(startAnchor);
  const end = source.indexOf(endAnchor, start);
  assert.ok(start >= 0 && end > start, `cannot slice ${startAnchor}`);
  return source.slice(start, end);
}

const followSource = slice('const conversationFollowKey =', 'async function trackNewConversations(');
const saveStart = source.indexOf('const save = () =>');
const saveSource = source.slice(saveStart, source.indexOf('\n', saveStart));

/** localStorage whose every operation fails, the way a full or blocked partition behaves. */
function blockedStorage() {
  const fail = () => { throw new Error('storage blocked'); };
  return { length: 0, key: fail, getItem: fail, setItem: fail, removeItem: fail };
}

/**
 * A storage that still reads but refuses to write. Reading has to work for the follow-list writers
 * to reach their write at all, which is what makes the guard observable.
 */
function writeBlockedStorage(entries) {
  return {
    get length() { return entries.size; },
    key: index => [...entries.keys()][index] ?? null,
    getItem: key => (entries.has(key) ? entries.get(key) : null),
    setItem: () => { throw new Error('storage blocked'); },
    removeItem: () => { throw new Error('storage blocked'); },
  };
}

test('a failing settings write does not escape the handler that flipped the switch', () => {
  const context = vm.createContext({ localStorage: blockedStorage(), settingsState: { settings: {} } });
  vm.runInContext(`${saveSource}\nglobalThis.save = save;`, context);
  assert.doesNotThrow(() => context.save());
});

test('a failing follow-list write does not turn a session load into a WeChat failure', () => {
  const entries = new Map();
  const context = vm.createContext({ localStorage: writeBlockedStorage(entries) });
  vm.runInContext(`${followSource}\nglobalThis.remember = rememberConversations;` +
    '\nglobalThis.forget = forgetConversationFollow;\nglobalThis.followKey = conversationFollowKey;',
    context);
  assert.doesNotThrow(() => context.remember('account-a', ['conversation-1']));
  entries.set(context.followKey, JSON.stringify({ 'account-a': { known: ['conversation-1'] } }));
  assert.doesNotThrow(() => context.forget('account-a'));
});

test('storage failures cannot prevent clearing the current account in memory', () => {
  const cleanup = slice('function clearLocalUiAnalysis(account)', 'byId("btnManageAnalysisCache")');
  for (const failure of ['length', 'key', 'removeItem']) {
    const current = JSON.stringify(['account-a', 'friend', 'member']);
    const other = JSON.stringify(['account-b', 'friend', 'member']);
    const ui = dom(), refreshed = [];
    const storage = {
      get length() { if (failure === 'length') throw Error('blocked length'); return 1; },
      key() { if (failure === 'key') throw Error('blocked key'); return 'mbti-unlocked:account-a:friend'; },
      removeItem() { throw Error('blocked remove'); },
    };
    const context = vm.createContext({ ...ui, localStorage: storage,
      saveStoredProfiles() {}, clearProfileView() { refreshed.push('profile'); },
      renderMessages() { refreshed.push('messages'); },
      setStripStatus(value) { refreshed.push(value); },
    });
    seed(context, { currentAccount: 'account-a', currentUser: 'friend', messages: [{ id: 'm1' }],
      results: { m1: { label: 'synthetic' } }, conversationMood: 'synthetic',
      storedProfileSnapshots: new Map([[current, {}], [other, {}]]),
      profileCache: new Map([[current, {}], [other, {}]]),
      profileRateSamples: new Map([[current, {}], [other, {}]]),
      autoIncrementalState: new Map([[current, {}], [other, {}]]),
      profileSnapshotsRequireRefresh: new Set([current, other]),
      requestedRecentSignatures: new Set(['synthetic']),
      sessionCache: new Map([['one', { account: 'account-a', results: { m1: {} }, mood: 'synthetic' }],
        ['two', { account: 'account-b', results: { m2: {} }, mood: 'retained' }]]),
    });
    vm.runInContext(cleanup + '\nthis.clear = clearLocalUiAnalysis;', context);
    assert.doesNotThrow(() => context.clear('account-a'), failure);
    assert.equal(context.results.m1, undefined);
    assert.equal(context.conversationMood, null);
    assert.equal(context.profileCache.has(current), false);
    assert.equal(context.profileCache.has(other), true);
    assert.equal(context.sessionCache.get('one').mood, null);
    assert.equal(context.sessionCache.get('two').mood, 'retained');
    assert.deepEqual(refreshed, ['profile', '画像缓存已清除', 'messages']);
  }
});
