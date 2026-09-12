/* engine.js — 核心引擎（签名冻结，见 DESIGN.md §2.1 / §2.3） */
(function (global) {
  'use strict';

  var W = 960, H = 540;
  var GROUND_Y = 460;
  var GRAVITY = 0.9;
  var MAX_FALL = 20;
  var WALL_L = 40, WALL_R = 920;
  var FPS = 60, STEP = 1 / 60;

  // 招式表：每条含 name/startup/active/recovery/damage/range/knockback/hitstun/input
  var MOVES = {
    light:   { name: '轻拳', input: 'J', startup: 4,  active: 3, recovery: 6,  damage: 40,  range: 50, knockback: 4, hitstun: 10 },
    heavy:   { name: '重拳', input: 'K', startup: 10, active: 4, recovery: 16, damage: 110, range: 62, knockback: 9, hitstun: 16 },
    kick:    { name: '踢腿', input: 'L', startup: 8,  active: 5, recovery: 14, damage: 85,  range: 70, knockback: 7, hitstun: 14 },
    special: { name: '必杀', input: 'U', startup: 12, active: 6, recovery: 22, damage: 130, range: 78, knockback: 6, hitstun: 18 }
  };

  function createFighter(opts) {
    opts = opts || {};
    var stats = opts.stats || {};
    var maxHp = stats.hp || 1000;
    return {
      id: opts.id || 'balanced',
      x: opts.x != null ? opts.x : 480,
      y: GROUND_Y,
      vx: 0, vy: 0,
      hp: maxHp, maxHp: maxHp,
      state: 'idle', stateTimer: 0,
      facing: opts.facing || 1,
      hitbox: null,
      hitstun: 0,
      blocking: false,
      onGround: true,
      cooldowns: { light: 0, heavy: 0, kick: 0, special: 0 },
      isAI: !!opts.isAI,
      move: null,
      _hits: {},
      stats: {
        moveSpeed: stats.moveSpeed != null ? stats.moveSpeed : 3.2,
        jump: stats.jump != null ? stats.jump : -15,
        damageScale: stats.damageScale != null ? stats.damageScale : 1,
        weight: stats.weight != null ? stats.weight : 1,
        color: stats.color || '#e8e8e8'
      }
    };
  }

  function hurtbox(f) {
    var w = 56, h = f.state === 'crouch' || f.blocking ? 78 : 130;
    return { x: f.x - w / 2, y: f.y - h, w: w, h: h };
  }

  function activeHitbox(f) {
    if (f.state !== 'attack' || !f.move) return null;
    var m = MOVES[f.move];
    var t = f.stateTimer;
    if (t < m.startup || t >= m.startup + m.active) return null;
    var w = 44, h = 34;
    return {
      x: f.facing > 0 ? f.x + 10 : f.x - 10 - w,
      y: f.y - 100,
      w: w, h: h,
      move: f.move
    };
  }

  // detectHit：a=攻击者，b=防御者 → {hit, damage, knockback, hitstun}
  function detectHit(a, b) {
    var miss = { hit: false, damage: 0, knockback: 0, hitstun: 0 };
    var hb = activeHitbox(a);
    if (!hb) return miss;
    var m = MOVES[hb.move];
    // 同一招一次 active 内每个目标只命中一次
    var key = hb.move + '_' + (a.stateTimer >= m.startup + m.active);
    if (a._hits[key]) return miss;
    var dist = Math.abs((a.x + a.facing * 20) - b.x);
    if (dist > m.range) return miss;
    var bb = hurtbox(b);
    if (!(hb.x < bb.x + bb.w && hb.x + hb.w > bb.x && hb.y < bb.y + bb.h && hb.y + hb.h > bb.y)) {
      if (dist > m.range) return miss;
    }
    a._hits[key] = true;
    var blocked = b.blocking && b.state !== 'hitstun' && b.state !== 'ko';
    var dmg = Math.round(m.damage * (a.stats.damageScale || 1));
    if (blocked) {
      return { hit: true, damage: Math.round(dmg * 0.15), knockback: m.knockback * 0.35, hitstun: 0 };
    }
    return { hit: true, damage: dmg, knockback: m.knockback * b.stats.weight, hitstun: m.hitstun * b.stats.weight };
  }

  function tryAttack(f, input) {
    var pick = input.special ? 'special' : input.heavy ? 'heavy' : input.kick ? 'kick' : input.light ? 'light' : null;
    if (!pick) return false;
    if (f.cooldowns[pick] > 0) return false;
    f.state = 'attack';
    f.stateTimer = 0;
    f.move = pick;
    f.hitbox = null;
    f._hits = {};
    return true;
  }

  // stepFighter：dt 固定 1/60，原地更新 fighter
  function stepFighter(f, input, opponent, dt) {
    input = input || {};
    if (f.state === 'ko') {
      f.vy += GRAVITY;
      f.y = Math.min(GROUND_Y, f.y + f.vy);
      return f;
    }
    // 冷却递减
    for (var k in f.cooldowns) { if (f.cooldowns[k] > 0) f.cooldowns[k]--; }

    // 面向对手
    if (opponent) f.facing = opponent.x >= f.x ? 1 : -1;

    // 硬直
    if (f.state === 'hitstun') {
      f.stateTimer++;
      if (f.stateTimer >= f.hitstun) { f.state = 'idle'; f.stateTimer = 0; f.hitstun = 0; }
    } else if (f.state === 'attack') {
      f.stateTimer++;
      var m = MOVES[f.move];
      f.hitbox = activeHitbox(f);
      if (f.hitbox) {
        var r = detectHit(f, opponent);
        if (r.hit && opponent) {
          opponent.hp = Math.max(0, opponent.hp - r.damage);
          opponent.vx = f.facing * r.knockback;
          if (r.hitstun > 0) { opponent.state = 'hitstun'; opponent.stateTimer = 0; opponent.hitstun = r.hitstun; }
          if (opponent.hp <= 0) { opponent.state = 'ko'; opponent.stateTimer = 0; opponent.vx = 0; }
          f.cooldowns[f.move] = m.startup + m.active + m.recovery;
        }
      }
      if (f.stateTimer >= m.startup + m.active + m.recovery) { f.state = 'idle'; f.stateTimer = 0; f.move = null; f.hitbox = null; }
    } else {
      // idle / walk / crouch
      f.blocking = !!input.block && f.onGround;
      tryAttack(f, input);
      if (f.state !== 'attack') {
        if (input.jump && f.onGround) {
          f.vy = f.stats.jump; f.onGround = false; f.state = 'jump';
        } else if (input.crouch) {
          f.state = 'crouch'; f.vx = 0;
        } else if (input.left || input.right) {
          f.vx = (input.right ? 1 : -1) * f.stats.moveSpeed;
          f.state = 'walk';
        } else {
          f.vx = 0; f.state = 'idle';
        }
      }
    }

    // 物理
    f.x += f.vx;
    if (!f.onGround || f.state === 'jump') {
      f.vy += GRAVITY;
      if (f.vy > MAX_FALL) f.vy = MAX_FALL;
      f.y += f.vy;
      if (f.y >= GROUND_Y) { f.y = GROUND_Y; f.vy = 0; f.onGround = true; if (f.state === 'jump') f.state = 'idle'; }
      f.vx *= 0.98;
    } else {
      f.vx *= 0.8;
      if (Math.abs(f.vx) < 0.1) f.vx = 0;
    }
    if (f.x < WALL_L) f.x = WALL_L;
    if (f.x > WALL_R) f.x = WALL_R;
    return f;
  }

  function drawFighter(ctx, f) {
    var h = f.state === 'crouch' ? 80 : 130;
    var cx = f.x, base = f.y;
    ctx.save();
    ctx.fillStyle = f.stats.color;
    // 躯干
    ctx.fillRect(cx - 20, base - h, 40, h * 0.6);
    // 头
    ctx.beginPath();
    ctx.arc(cx, base - h - 14, 16, 0, Math.PI * 2);
    ctx.fillStyle = '#f0c088';
    ctx.fill();
    // 腿
    ctx.fillStyle = f.stats.color;
    ctx.fillRect(cx - 16, base - h * 0.4, 12, h * 0.4);
    ctx.fillRect(cx + 4, base - h * 0.4, 12, h * 0.4);
    // 攻击判定可视化（出招时前手前伸）
    if (f.state === 'attack' && f.hitbox) {
      ctx.fillStyle = 'rgba(255,80,80,0.35)';
      ctx.fillRect(f.hitbox.x, f.hitbox.y, f.hitbox.w, f.hitbox.h);
    }
    ctx.restore();
  }

  global.FG = global.FG || {};
  global.FG.Engine = {
    WIDTH: W, HEIGHT: H, GROUND_Y: GROUND_Y, GRAVITY: GRAVITY, FPS: FPS, STEP: STEP,
    MOVES: MOVES,
    createFighter: createFighter,
    stepFighter: stepFighter,
    detectHit: detectHit,
    drawFighter: drawFighter,
    hurtbox: hurtbox,
    activeHitbox: activeHitbox
  };
})(window);
