/* ai.js — AI 对手（见 DESIGN.md §2.4 / §5） */
(function (global) {
  'use strict';
  var FG = global.FG = global.FG || {};
  var MOVES = FG.Engine.MOVES;

  var DIFFS = {
    easy:   { delayFrames: 20, aggression: 0.30, decisionCooldown: 24, blockChance: 0.15, specialChance: 0.10 },
    normal: { delayFrames: 12, aggression: 0.55, decisionCooldown: 16, blockChance: 0.40, specialChance: 0.25 },
    hard:   { delayFrames: 6,  aggression: 0.80, decisionCooldown: 10, blockChance: 0.70, specialChance: 0.45 }
  };

  var diff = DIFFS.normal;
  var applyUntil = 0;      // 当前动作持续到第几帧
  var cur = {};            // 当前输入
  var tick = 0;

  function emptyInput() {
    return { left: false, right: false, jump: false, crouch: false,
             light: false, heavy: false, kick: false, special: false, block: false };
  }

  function zoneOf(dx) { return dx > 300 ? 'far' : dx > 150 ? 'mid' : 'near'; }

  function setDifficulty(level) { diff = DIFFS[level] || DIFFS.normal; }
  function reset() { cur = emptyInput(); tick = 0; applyUntil = 0; }

  // decide(self, opponent, dt) → input 对象（与人类同构）
  function decide(self, opponent, dt) {
    tick++;
    if (!opponent) return emptyInput();

    // 保持当前决策直到冷却结束
    if (tick < applyUntil) return cur;

    var dx = Math.abs(opponent.x - self.x);
    var zone = zoneOf(dx);
    var toRight = opponent.x > self.x;
    var inp = emptyInput();

    // 受击/硬直时不决策
    if (self.state === 'hitstun' || self.state === 'attack' || self.state === 'ko') {
      applyUntil = tick + 4;
      cur = inp;
      return inp;
    }

    // 对手正在出招 → 按 blockChance 防御
    if (opponent.state === 'attack' && Math.random() < diff.blockChance) {
      inp.block = true; inp.crouch = true;
      cur = inp; applyUntil = tick + 8;
      return inp;
    }

    var r = Math.random();
    if (r < diff.aggression) {
      // 攻击：近距离随机拳腿，中远距离用必杀
      if (zone === 'near') {
        var a = Math.random();
        if (a < 0.5) inp.light = true; else if (a < 0.8) inp.heavy = true; else inp.kick = true;
      } else if (Math.random() < diff.specialChance) {
        inp.special = true;
      } else {
        inp[left] = !toRight; inp[right] = toRight; // 前进
      }
    } else {
      // 移动/防御
      var m = Math.random();
      if (m < 0.55) { inp[left] = !toRight; inp.right = toRight; }
      else if (m < 0.75) { inp.jump = true; }
      else if (m < 0.9) { inp[left] = toRight; inp.right = !toRight; } // 后撤
      else { inp.block = true; inp.crouch = true; }
    }

    cur = inp;
    applyUntil = tick + diff.decisionCooldown;
    return inp;
  }

  FG.AI = { decide: decide, setDifficulty: setDifficulty, reset: reset, DIFFS: DIFFS };
})(window);
