"use strict";
(() => {
  var __create = Object.create;
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __getProtoOf = Object.getPrototypeOf;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __commonJS = (cb, mod) => function __require() {
    try {
      return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
    } catch (e) {
      throw mod = 0, e;
    }
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
    // If the importer is in node compatibility mode or this is not an ESM
    // file that has been converted to a CommonJS file using a Babel-
    // compatible transform (i.e. "__esModule" has not been set), then set
    // "default" to the CommonJS "module.exports" for node compatibility.
    isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
    mod
  ));

  // node_modules/graphology/dist/graphology.umd.min.js
  var require_graphology_umd_min = __commonJS({
    "node_modules/graphology/dist/graphology.umd.min.js"(exports, module) {
      !(function(t, e) {
        "object" == typeof exports && "undefined" != typeof module ? module.exports = e() : "function" == typeof define && define.amd ? define(e) : (t = "undefined" != typeof globalThis ? globalThis : t || self).graphology = e();
      })(exports, (function() {
        "use strict";
        function t(e2) {
          return t = "function" == typeof Symbol && "symbol" == typeof Symbol.iterator ? function(t2) {
            return typeof t2;
          } : function(t2) {
            return t2 && "function" == typeof Symbol && t2.constructor === Symbol && t2 !== Symbol.prototype ? "symbol" : typeof t2;
          }, t(e2);
        }
        function e(t2, e2) {
          t2.prototype = Object.create(e2.prototype), t2.prototype.constructor = t2, r(t2, e2);
        }
        function n(t2) {
          return n = Object.setPrototypeOf ? Object.getPrototypeOf.bind() : function(t3) {
            return t3.__proto__ || Object.getPrototypeOf(t3);
          }, n(t2);
        }
        function r(t2, e2) {
          return r = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t3, e3) {
            return t3.__proto__ = e3, t3;
          }, r(t2, e2);
        }
        function i() {
          if ("undefined" == typeof Reflect || !Reflect.construct) return false;
          if (Reflect.construct.sham) return false;
          if ("function" == typeof Proxy) return true;
          try {
            return Boolean.prototype.valueOf.call(Reflect.construct(Boolean, [], (function() {
            }))), true;
          } catch (t2) {
            return false;
          }
        }
        function o(t2, e2, n2) {
          return o = i() ? Reflect.construct.bind() : function(t3, e3, n3) {
            var i2 = [null];
            i2.push.apply(i2, e3);
            var o2 = new (Function.bind.apply(t3, i2))();
            return n3 && r(o2, n3.prototype), o2;
          }, o.apply(null, arguments);
        }
        function a(t2) {
          var e2 = "function" == typeof Map ? /* @__PURE__ */ new Map() : void 0;
          return a = function(t3) {
            if (null === t3 || (i2 = t3, -1 === Function.toString.call(i2).indexOf("[native code]"))) return t3;
            var i2;
            if ("function" != typeof t3) throw new TypeError("Super expression must either be null or a function");
            if (void 0 !== e2) {
              if (e2.has(t3)) return e2.get(t3);
              e2.set(t3, a2);
            }
            function a2() {
              return o(t3, arguments, n(this).constructor);
            }
            return a2.prototype = Object.create(t3.prototype, { constructor: { value: a2, enumerable: false, writable: true, configurable: true } }), r(a2, t3);
          }, a(t2);
        }
        function c(t2) {
          if (void 0 === t2) throw new ReferenceError("this hasn't been initialised - super() hasn't been called");
          return t2;
        }
        var u = function() {
          for (var t2 = arguments[0], e2 = 1, n2 = arguments.length; e2 < n2; e2++) if (arguments[e2]) for (var r2 in arguments[e2]) t2[r2] = arguments[e2][r2];
          return t2;
        };
        function d(t2, e2, n2, r2) {
          var i2 = t2._nodes.get(e2), o2 = null;
          return i2 ? o2 = "mixed" === r2 ? i2.out && i2.out[n2] || i2.undirected && i2.undirected[n2] : "directed" === r2 ? i2.out && i2.out[n2] : i2.undirected && i2.undirected[n2] : o2;
        }
        function s(e2) {
          return "object" === t(e2) && null !== e2;
        }
        function h(t2) {
          var e2;
          for (e2 in t2) return false;
          return true;
        }
        function p(t2, e2, n2) {
          Object.defineProperty(t2, e2, { enumerable: false, configurable: false, writable: true, value: n2 });
        }
        function f(t2, e2, n2) {
          var r2 = { enumerable: true, configurable: true };
          "function" == typeof n2 ? r2.get = n2 : (r2.value = n2, r2.writable = false), Object.defineProperty(t2, e2, r2);
        }
        function l(t2) {
          return !!s(t2) && !(t2.attributes && !Array.isArray(t2.attributes));
        }
        "function" == typeof Object.assign && (u = Object.assign);
        var g, y = { exports: {} }, w = "object" == typeof Reflect ? Reflect : null, v = w && "function" == typeof w.apply ? w.apply : function(t2, e2, n2) {
          return Function.prototype.apply.call(t2, e2, n2);
        };
        g = w && "function" == typeof w.ownKeys ? w.ownKeys : Object.getOwnPropertySymbols ? function(t2) {
          return Object.getOwnPropertyNames(t2).concat(Object.getOwnPropertySymbols(t2));
        } : function(t2) {
          return Object.getOwnPropertyNames(t2);
        };
        var b = Number.isNaN || function(t2) {
          return t2 != t2;
        };
        function m() {
          m.init.call(this);
        }
        y.exports = m, y.exports.once = function(t2, e2) {
          return new Promise((function(n2, r2) {
            function i2(n3) {
              t2.removeListener(e2, o2), r2(n3);
            }
            function o2() {
              "function" == typeof t2.removeListener && t2.removeListener("error", i2), n2([].slice.call(arguments));
            }
            U(t2, e2, o2, { once: true }), "error" !== e2 && (function(t3, e3, n3) {
              "function" == typeof t3.on && U(t3, "error", e3, n3);
            })(t2, i2, { once: true });
          }));
        }, m.EventEmitter = m, m.prototype._events = void 0, m.prototype._eventsCount = 0, m.prototype._maxListeners = void 0;
        var k = 10;
        function _(t2) {
          if ("function" != typeof t2) throw new TypeError('The "listener" argument must be of type Function. Received type ' + typeof t2);
        }
        function G(t2) {
          return void 0 === t2._maxListeners ? m.defaultMaxListeners : t2._maxListeners;
        }
        function x(t2, e2, n2, r2) {
          var i2, o2, a2, c2;
          if (_(n2), void 0 === (o2 = t2._events) ? (o2 = t2._events = /* @__PURE__ */ Object.create(null), t2._eventsCount = 0) : (void 0 !== o2.newListener && (t2.emit("newListener", e2, n2.listener ? n2.listener : n2), o2 = t2._events), a2 = o2[e2]), void 0 === a2) a2 = o2[e2] = n2, ++t2._eventsCount;
          else if ("function" == typeof a2 ? a2 = o2[e2] = r2 ? [n2, a2] : [a2, n2] : r2 ? a2.unshift(n2) : a2.push(n2), (i2 = G(t2)) > 0 && a2.length > i2 && !a2.warned) {
            a2.warned = true;
            var u2 = new Error("Possible EventEmitter memory leak detected. " + a2.length + " " + String(e2) + " listeners added. Use emitter.setMaxListeners() to increase limit");
            u2.name = "MaxListenersExceededWarning", u2.emitter = t2, u2.type = e2, u2.count = a2.length, c2 = u2, console && console.warn && console.warn(c2);
          }
          return t2;
        }
        function E() {
          if (!this.fired) return this.target.removeListener(this.type, this.wrapFn), this.fired = true, 0 === arguments.length ? this.listener.call(this.target) : this.listener.apply(this.target, arguments);
        }
        function A(t2, e2, n2) {
          var r2 = { fired: false, wrapFn: void 0, target: t2, type: e2, listener: n2 }, i2 = E.bind(r2);
          return i2.listener = n2, r2.wrapFn = i2, i2;
        }
        function L(t2, e2, n2) {
          var r2 = t2._events;
          if (void 0 === r2) return [];
          var i2 = r2[e2];
          return void 0 === i2 ? [] : "function" == typeof i2 ? n2 ? [i2.listener || i2] : [i2] : n2 ? (function(t3) {
            for (var e3 = new Array(t3.length), n3 = 0; n3 < e3.length; ++n3) e3[n3] = t3[n3].listener || t3[n3];
            return e3;
          })(i2) : D(i2, i2.length);
        }
        function S(t2) {
          var e2 = this._events;
          if (void 0 !== e2) {
            var n2 = e2[t2];
            if ("function" == typeof n2) return 1;
            if (void 0 !== n2) return n2.length;
          }
          return 0;
        }
        function D(t2, e2) {
          for (var n2 = new Array(e2), r2 = 0; r2 < e2; ++r2) n2[r2] = t2[r2];
          return n2;
        }
        function U(t2, e2, n2, r2) {
          if ("function" == typeof t2.on) r2.once ? t2.once(e2, n2) : t2.on(e2, n2);
          else {
            if ("function" != typeof t2.addEventListener) throw new TypeError('The "emitter" argument must be of type EventEmitter. Received type ' + typeof t2);
            t2.addEventListener(e2, (function i2(o2) {
              r2.once && t2.removeEventListener(e2, i2), n2(o2);
            }));
          }
        }
        function N(t2) {
          if ("function" != typeof t2) throw new Error("obliterator/iterator: expecting a function!");
          this.next = t2;
        }
        Object.defineProperty(m, "defaultMaxListeners", { enumerable: true, get: function() {
          return k;
        }, set: function(t2) {
          if ("number" != typeof t2 || t2 < 0 || b(t2)) throw new RangeError('The value of "defaultMaxListeners" is out of range. It must be a non-negative number. Received ' + t2 + ".");
          k = t2;
        } }), m.init = function() {
          void 0 !== this._events && this._events !== Object.getPrototypeOf(this)._events || (this._events = /* @__PURE__ */ Object.create(null), this._eventsCount = 0), this._maxListeners = this._maxListeners || void 0;
        }, m.prototype.setMaxListeners = function(t2) {
          if ("number" != typeof t2 || t2 < 0 || b(t2)) throw new RangeError('The value of "n" is out of range. It must be a non-negative number. Received ' + t2 + ".");
          return this._maxListeners = t2, this;
        }, m.prototype.getMaxListeners = function() {
          return G(this);
        }, m.prototype.emit = function(t2) {
          for (var e2 = [], n2 = 1; n2 < arguments.length; n2++) e2.push(arguments[n2]);
          var r2 = "error" === t2, i2 = this._events;
          if (void 0 !== i2) r2 = r2 && void 0 === i2.error;
          else if (!r2) return false;
          if (r2) {
            var o2;
            if (e2.length > 0 && (o2 = e2[0]), o2 instanceof Error) throw o2;
            var a2 = new Error("Unhandled error." + (o2 ? " (" + o2.message + ")" : ""));
            throw a2.context = o2, a2;
          }
          var c2 = i2[t2];
          if (void 0 === c2) return false;
          if ("function" == typeof c2) v(c2, this, e2);
          else {
            var u2 = c2.length, d2 = D(c2, u2);
            for (n2 = 0; n2 < u2; ++n2) v(d2[n2], this, e2);
          }
          return true;
        }, m.prototype.addListener = function(t2, e2) {
          return x(this, t2, e2, false);
        }, m.prototype.on = m.prototype.addListener, m.prototype.prependListener = function(t2, e2) {
          return x(this, t2, e2, true);
        }, m.prototype.once = function(t2, e2) {
          return _(e2), this.on(t2, A(this, t2, e2)), this;
        }, m.prototype.prependOnceListener = function(t2, e2) {
          return _(e2), this.prependListener(t2, A(this, t2, e2)), this;
        }, m.prototype.removeListener = function(t2, e2) {
          var n2, r2, i2, o2, a2;
          if (_(e2), void 0 === (r2 = this._events)) return this;
          if (void 0 === (n2 = r2[t2])) return this;
          if (n2 === e2 || n2.listener === e2) 0 == --this._eventsCount ? this._events = /* @__PURE__ */ Object.create(null) : (delete r2[t2], r2.removeListener && this.emit("removeListener", t2, n2.listener || e2));
          else if ("function" != typeof n2) {
            for (i2 = -1, o2 = n2.length - 1; o2 >= 0; o2--) if (n2[o2] === e2 || n2[o2].listener === e2) {
              a2 = n2[o2].listener, i2 = o2;
              break;
            }
            if (i2 < 0) return this;
            0 === i2 ? n2.shift() : (function(t3, e3) {
              for (; e3 + 1 < t3.length; e3++) t3[e3] = t3[e3 + 1];
              t3.pop();
            })(n2, i2), 1 === n2.length && (r2[t2] = n2[0]), void 0 !== r2.removeListener && this.emit("removeListener", t2, a2 || e2);
          }
          return this;
        }, m.prototype.off = m.prototype.removeListener, m.prototype.removeAllListeners = function(t2) {
          var e2, n2, r2;
          if (void 0 === (n2 = this._events)) return this;
          if (void 0 === n2.removeListener) return 0 === arguments.length ? (this._events = /* @__PURE__ */ Object.create(null), this._eventsCount = 0) : void 0 !== n2[t2] && (0 == --this._eventsCount ? this._events = /* @__PURE__ */ Object.create(null) : delete n2[t2]), this;
          if (0 === arguments.length) {
            var i2, o2 = Object.keys(n2);
            for (r2 = 0; r2 < o2.length; ++r2) "removeListener" !== (i2 = o2[r2]) && this.removeAllListeners(i2);
            return this.removeAllListeners("removeListener"), this._events = /* @__PURE__ */ Object.create(null), this._eventsCount = 0, this;
          }
          if ("function" == typeof (e2 = n2[t2])) this.removeListener(t2, e2);
          else if (void 0 !== e2) for (r2 = e2.length - 1; r2 >= 0; r2--) this.removeListener(t2, e2[r2]);
          return this;
        }, m.prototype.listeners = function(t2) {
          return L(this, t2, true);
        }, m.prototype.rawListeners = function(t2) {
          return L(this, t2, false);
        }, m.listenerCount = function(t2, e2) {
          return "function" == typeof t2.listenerCount ? t2.listenerCount(e2) : S.call(t2, e2);
        }, m.prototype.listenerCount = S, m.prototype.eventNames = function() {
          return this._eventsCount > 0 ? g(this._events) : [];
        }, "undefined" != typeof Symbol && (N.prototype[Symbol.iterator] = function() {
          return this;
        }), N.of = function() {
          var t2 = arguments, e2 = t2.length, n2 = 0;
          return new N((function() {
            return n2 >= e2 ? { done: true } : { done: false, value: t2[n2++] };
          }));
        }, N.empty = function() {
          return new N((function() {
            return { done: true };
          }));
        }, N.fromSequence = function(t2) {
          var e2 = 0, n2 = t2.length;
          return new N((function() {
            return e2 >= n2 ? { done: true } : { done: false, value: t2[e2++] };
          }));
        }, N.is = function(t2) {
          return t2 instanceof N || "object" == typeof t2 && null !== t2 && "function" == typeof t2.next;
        };
        var O = N, j = {};
        j.ARRAY_BUFFER_SUPPORT = "undefined" != typeof ArrayBuffer, j.SYMBOL_SUPPORT = "undefined" != typeof Symbol;
        var C = O, M = j, z = M.ARRAY_BUFFER_SUPPORT, W = M.SYMBOL_SUPPORT;
        var P = function(t2) {
          var e2 = (function(t3) {
            return "string" == typeof t3 || Array.isArray(t3) || z && ArrayBuffer.isView(t3) ? C.fromSequence(t3) : "object" != typeof t3 || null === t3 ? null : W && "function" == typeof t3[Symbol.iterator] ? t3[Symbol.iterator]() : "function" == typeof t3.next ? t3 : null;
          })(t2);
          if (!e2) throw new Error("obliterator: target is not iterable nor a valid iterator.");
          return e2;
        }, R = P, K = function(t2, e2) {
          for (var n2, r2 = arguments.length > 1 ? e2 : 1 / 0, i2 = r2 !== 1 / 0 ? new Array(r2) : [], o2 = 0, a2 = R(t2); ; ) {
            if (o2 === r2) return i2;
            if ((n2 = a2.next()).done) return o2 !== e2 && (i2.length = o2), i2;
            i2[o2++] = n2.value;
          }
        }, T = (function(t2) {
          function n2(e2) {
            var n3;
            return (n3 = t2.call(this) || this).name = "GraphError", n3.message = e2, n3;
          }
          return e(n2, t2), n2;
        })(a(Error)), B = (function(t2) {
          function n2(e2) {
            var r2;
            return (r2 = t2.call(this, e2) || this).name = "InvalidArgumentsGraphError", "function" == typeof Error.captureStackTrace && Error.captureStackTrace(c(r2), n2.prototype.constructor), r2;
          }
          return e(n2, t2), n2;
        })(T), F = (function(t2) {
          function n2(e2) {
            var r2;
            return (r2 = t2.call(this, e2) || this).name = "NotFoundGraphError", "function" == typeof Error.captureStackTrace && Error.captureStackTrace(c(r2), n2.prototype.constructor), r2;
          }
          return e(n2, t2), n2;
        })(T), I = (function(t2) {
          function n2(e2) {
            var r2;
            return (r2 = t2.call(this, e2) || this).name = "UsageGraphError", "function" == typeof Error.captureStackTrace && Error.captureStackTrace(c(r2), n2.prototype.constructor), r2;
          }
          return e(n2, t2), n2;
        })(T);
        function Y(t2, e2) {
          this.key = t2, this.attributes = e2, this.clear();
        }
        function q(t2, e2) {
          this.key = t2, this.attributes = e2, this.clear();
        }
        function J(t2, e2) {
          this.key = t2, this.attributes = e2, this.clear();
        }
        function V(t2, e2, n2, r2, i2) {
          this.key = e2, this.attributes = i2, this.undirected = t2, this.source = n2, this.target = r2;
        }
        Y.prototype.clear = function() {
          this.inDegree = 0, this.outDegree = 0, this.undirectedDegree = 0, this.undirectedLoops = 0, this.directedLoops = 0, this.in = {}, this.out = {}, this.undirected = {};
        }, q.prototype.clear = function() {
          this.inDegree = 0, this.outDegree = 0, this.directedLoops = 0, this.in = {}, this.out = {};
        }, J.prototype.clear = function() {
          this.undirectedDegree = 0, this.undirectedLoops = 0, this.undirected = {};
        }, V.prototype.attach = function() {
          var t2 = "out", e2 = "in";
          this.undirected && (t2 = e2 = "undirected");
          var n2 = this.source.key, r2 = this.target.key;
          this.source[t2][r2] = this, this.undirected && n2 === r2 || (this.target[e2][n2] = this);
        }, V.prototype.attachMulti = function() {
          var t2 = "out", e2 = "in", n2 = this.source.key, r2 = this.target.key;
          this.undirected && (t2 = e2 = "undirected");
          var i2 = this.source[t2], o2 = i2[r2];
          if (void 0 === o2) return i2[r2] = this, void (this.undirected && n2 === r2 || (this.target[e2][n2] = this));
          o2.previous = this, this.next = o2, i2[r2] = this, this.target[e2][n2] = this;
        }, V.prototype.detach = function() {
          var t2 = this.source.key, e2 = this.target.key, n2 = "out", r2 = "in";
          this.undirected && (n2 = r2 = "undirected"), delete this.source[n2][e2], delete this.target[r2][t2];
        }, V.prototype.detachMulti = function() {
          var t2 = this.source.key, e2 = this.target.key, n2 = "out", r2 = "in";
          this.undirected && (n2 = r2 = "undirected"), void 0 === this.previous ? void 0 === this.next ? (delete this.source[n2][e2], delete this.target[r2][t2]) : (this.next.previous = void 0, this.source[n2][e2] = this.next, this.target[r2][t2] = this.next) : (this.previous.next = this.next, void 0 !== this.next && (this.next.previous = this.previous));
        };
        function H(t2, e2, n2, r2, i2, o2, a2) {
          var c2, u2, d2, s2;
          if (r2 = "" + r2, 0 === n2) {
            if (!(c2 = t2._nodes.get(r2))) throw new F("Graph.".concat(e2, ': could not find the "').concat(r2, '" node in the graph.'));
            d2 = i2, s2 = o2;
          } else if (3 === n2) {
            if (i2 = "" + i2, !(u2 = t2._edges.get(i2))) throw new F("Graph.".concat(e2, ': could not find the "').concat(i2, '" edge in the graph.'));
            var h2 = u2.source.key, p2 = u2.target.key;
            if (r2 === h2) c2 = u2.target;
            else {
              if (r2 !== p2) throw new F("Graph.".concat(e2, ': the "').concat(r2, '" node is not attached to the "').concat(i2, '" edge (').concat(h2, ", ").concat(p2, ")."));
              c2 = u2.source;
            }
            d2 = o2, s2 = a2;
          } else {
            if (!(u2 = t2._edges.get(r2))) throw new F("Graph.".concat(e2, ': could not find the "').concat(r2, '" edge in the graph.'));
            c2 = 1 === n2 ? u2.source : u2.target, d2 = i2, s2 = o2;
          }
          return [c2, d2, s2];
        }
        var Q = [{ name: function(t2) {
          return "get".concat(t2, "Attribute");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2, i2) {
            var o2 = H(this, e2, n2, t3, r2, i2), a2 = o2[0], c2 = o2[1];
            return a2.attributes[c2];
          };
        } }, { name: function(t2) {
          return "get".concat(t2, "Attributes");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2) {
            return H(this, e2, n2, t3, r2)[0].attributes;
          };
        } }, { name: function(t2) {
          return "has".concat(t2, "Attribute");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2, i2) {
            var o2 = H(this, e2, n2, t3, r2, i2), a2 = o2[0], c2 = o2[1];
            return a2.attributes.hasOwnProperty(c2);
          };
        } }, { name: function(t2) {
          return "set".concat(t2, "Attribute");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2, i2, o2) {
            var a2 = H(this, e2, n2, t3, r2, i2, o2), c2 = a2[0], u2 = a2[1], d2 = a2[2];
            return c2.attributes[u2] = d2, this.emit("nodeAttributesUpdated", { key: c2.key, type: "set", attributes: c2.attributes, name: u2 }), this;
          };
        } }, { name: function(t2) {
          return "update".concat(t2, "Attribute");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2, i2, o2) {
            var a2 = H(this, e2, n2, t3, r2, i2, o2), c2 = a2[0], u2 = a2[1], d2 = a2[2];
            if ("function" != typeof d2) throw new B("Graph.".concat(e2, ": updater should be a function."));
            var s2 = c2.attributes, h2 = d2(s2[u2]);
            return s2[u2] = h2, this.emit("nodeAttributesUpdated", { key: c2.key, type: "set", attributes: c2.attributes, name: u2 }), this;
          };
        } }, { name: function(t2) {
          return "remove".concat(t2, "Attribute");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2, i2) {
            var o2 = H(this, e2, n2, t3, r2, i2), a2 = o2[0], c2 = o2[1];
            return delete a2.attributes[c2], this.emit("nodeAttributesUpdated", { key: a2.key, type: "remove", attributes: a2.attributes, name: c2 }), this;
          };
        } }, { name: function(t2) {
          return "replace".concat(t2, "Attributes");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2, i2) {
            var o2 = H(this, e2, n2, t3, r2, i2), a2 = o2[0], c2 = o2[1];
            if (!s(c2)) throw new B("Graph.".concat(e2, ": provided attributes are not a plain object."));
            return a2.attributes = c2, this.emit("nodeAttributesUpdated", { key: a2.key, type: "replace", attributes: a2.attributes }), this;
          };
        } }, { name: function(t2) {
          return "merge".concat(t2, "Attributes");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2, i2) {
            var o2 = H(this, e2, n2, t3, r2, i2), a2 = o2[0], c2 = o2[1];
            if (!s(c2)) throw new B("Graph.".concat(e2, ": provided attributes are not a plain object."));
            return u(a2.attributes, c2), this.emit("nodeAttributesUpdated", { key: a2.key, type: "merge", attributes: a2.attributes, data: c2 }), this;
          };
        } }, { name: function(t2) {
          return "update".concat(t2, "Attributes");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2, i2) {
            var o2 = H(this, e2, n2, t3, r2, i2), a2 = o2[0], c2 = o2[1];
            if ("function" != typeof c2) throw new B("Graph.".concat(e2, ": provided updater is not a function."));
            return a2.attributes = c2(a2.attributes), this.emit("nodeAttributesUpdated", { key: a2.key, type: "update", attributes: a2.attributes }), this;
          };
        } }];
        var X = [{ name: function(t2) {
          return "get".concat(t2, "Attribute");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2) {
            var i2;
            if ("mixed" !== this.type && "mixed" !== n2 && n2 !== this.type) throw new I("Graph.".concat(e2, ": cannot find this type of edges in your ").concat(this.type, " graph."));
            if (arguments.length > 2) {
              if (this.multi) throw new I("Graph.".concat(e2, ": cannot use a {source,target} combo when asking about an edge's attributes in a MultiGraph since we cannot infer the one you want information about."));
              var o2 = "" + t3, a2 = "" + r2;
              if (r2 = arguments[2], !(i2 = d(this, o2, a2, n2))) throw new F("Graph.".concat(e2, ': could not find an edge for the given path ("').concat(o2, '" - "').concat(a2, '").'));
            } else {
              if ("mixed" !== n2) throw new I("Graph.".concat(e2, ": calling this method with only a key (vs. a source and target) does not make sense since an edge with this key could have the other type."));
              if (t3 = "" + t3, !(i2 = this._edges.get(t3))) throw new F("Graph.".concat(e2, ': could not find the "').concat(t3, '" edge in the graph.'));
            }
            return i2.attributes[r2];
          };
        } }, { name: function(t2) {
          return "get".concat(t2, "Attributes");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3) {
            var r2;
            if ("mixed" !== this.type && "mixed" !== n2 && n2 !== this.type) throw new I("Graph.".concat(e2, ": cannot find this type of edges in your ").concat(this.type, " graph."));
            if (arguments.length > 1) {
              if (this.multi) throw new I("Graph.".concat(e2, ": cannot use a {source,target} combo when asking about an edge's attributes in a MultiGraph since we cannot infer the one you want information about."));
              var i2 = "" + t3, o2 = "" + arguments[1];
              if (!(r2 = d(this, i2, o2, n2))) throw new F("Graph.".concat(e2, ': could not find an edge for the given path ("').concat(i2, '" - "').concat(o2, '").'));
            } else {
              if ("mixed" !== n2) throw new I("Graph.".concat(e2, ": calling this method with only a key (vs. a source and target) does not make sense since an edge with this key could have the other type."));
              if (t3 = "" + t3, !(r2 = this._edges.get(t3))) throw new F("Graph.".concat(e2, ': could not find the "').concat(t3, '" edge in the graph.'));
            }
            return r2.attributes;
          };
        } }, { name: function(t2) {
          return "has".concat(t2, "Attribute");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2) {
            var i2;
            if ("mixed" !== this.type && "mixed" !== n2 && n2 !== this.type) throw new I("Graph.".concat(e2, ": cannot find this type of edges in your ").concat(this.type, " graph."));
            if (arguments.length > 2) {
              if (this.multi) throw new I("Graph.".concat(e2, ": cannot use a {source,target} combo when asking about an edge's attributes in a MultiGraph since we cannot infer the one you want information about."));
              var o2 = "" + t3, a2 = "" + r2;
              if (r2 = arguments[2], !(i2 = d(this, o2, a2, n2))) throw new F("Graph.".concat(e2, ': could not find an edge for the given path ("').concat(o2, '" - "').concat(a2, '").'));
            } else {
              if ("mixed" !== n2) throw new I("Graph.".concat(e2, ": calling this method with only a key (vs. a source and target) does not make sense since an edge with this key could have the other type."));
              if (t3 = "" + t3, !(i2 = this._edges.get(t3))) throw new F("Graph.".concat(e2, ': could not find the "').concat(t3, '" edge in the graph.'));
            }
            return i2.attributes.hasOwnProperty(r2);
          };
        } }, { name: function(t2) {
          return "set".concat(t2, "Attribute");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2, i2) {
            var o2;
            if ("mixed" !== this.type && "mixed" !== n2 && n2 !== this.type) throw new I("Graph.".concat(e2, ": cannot find this type of edges in your ").concat(this.type, " graph."));
            if (arguments.length > 3) {
              if (this.multi) throw new I("Graph.".concat(e2, ": cannot use a {source,target} combo when asking about an edge's attributes in a MultiGraph since we cannot infer the one you want information about."));
              var a2 = "" + t3, c2 = "" + r2;
              if (r2 = arguments[2], i2 = arguments[3], !(o2 = d(this, a2, c2, n2))) throw new F("Graph.".concat(e2, ': could not find an edge for the given path ("').concat(a2, '" - "').concat(c2, '").'));
            } else {
              if ("mixed" !== n2) throw new I("Graph.".concat(e2, ": calling this method with only a key (vs. a source and target) does not make sense since an edge with this key could have the other type."));
              if (t3 = "" + t3, !(o2 = this._edges.get(t3))) throw new F("Graph.".concat(e2, ': could not find the "').concat(t3, '" edge in the graph.'));
            }
            return o2.attributes[r2] = i2, this.emit("edgeAttributesUpdated", { key: o2.key, type: "set", attributes: o2.attributes, name: r2 }), this;
          };
        } }, { name: function(t2) {
          return "update".concat(t2, "Attribute");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2, i2) {
            var o2;
            if ("mixed" !== this.type && "mixed" !== n2 && n2 !== this.type) throw new I("Graph.".concat(e2, ": cannot find this type of edges in your ").concat(this.type, " graph."));
            if (arguments.length > 3) {
              if (this.multi) throw new I("Graph.".concat(e2, ": cannot use a {source,target} combo when asking about an edge's attributes in a MultiGraph since we cannot infer the one you want information about."));
              var a2 = "" + t3, c2 = "" + r2;
              if (r2 = arguments[2], i2 = arguments[3], !(o2 = d(this, a2, c2, n2))) throw new F("Graph.".concat(e2, ': could not find an edge for the given path ("').concat(a2, '" - "').concat(c2, '").'));
            } else {
              if ("mixed" !== n2) throw new I("Graph.".concat(e2, ": calling this method with only a key (vs. a source and target) does not make sense since an edge with this key could have the other type."));
              if (t3 = "" + t3, !(o2 = this._edges.get(t3))) throw new F("Graph.".concat(e2, ': could not find the "').concat(t3, '" edge in the graph.'));
            }
            if ("function" != typeof i2) throw new B("Graph.".concat(e2, ": updater should be a function."));
            return o2.attributes[r2] = i2(o2.attributes[r2]), this.emit("edgeAttributesUpdated", { key: o2.key, type: "set", attributes: o2.attributes, name: r2 }), this;
          };
        } }, { name: function(t2) {
          return "remove".concat(t2, "Attribute");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2) {
            var i2;
            if ("mixed" !== this.type && "mixed" !== n2 && n2 !== this.type) throw new I("Graph.".concat(e2, ": cannot find this type of edges in your ").concat(this.type, " graph."));
            if (arguments.length > 2) {
              if (this.multi) throw new I("Graph.".concat(e2, ": cannot use a {source,target} combo when asking about an edge's attributes in a MultiGraph since we cannot infer the one you want information about."));
              var o2 = "" + t3, a2 = "" + r2;
              if (r2 = arguments[2], !(i2 = d(this, o2, a2, n2))) throw new F("Graph.".concat(e2, ': could not find an edge for the given path ("').concat(o2, '" - "').concat(a2, '").'));
            } else {
              if ("mixed" !== n2) throw new I("Graph.".concat(e2, ": calling this method with only a key (vs. a source and target) does not make sense since an edge with this key could have the other type."));
              if (t3 = "" + t3, !(i2 = this._edges.get(t3))) throw new F("Graph.".concat(e2, ': could not find the "').concat(t3, '" edge in the graph.'));
            }
            return delete i2.attributes[r2], this.emit("edgeAttributesUpdated", { key: i2.key, type: "remove", attributes: i2.attributes, name: r2 }), this;
          };
        } }, { name: function(t2) {
          return "replace".concat(t2, "Attributes");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2) {
            var i2;
            if ("mixed" !== this.type && "mixed" !== n2 && n2 !== this.type) throw new I("Graph.".concat(e2, ": cannot find this type of edges in your ").concat(this.type, " graph."));
            if (arguments.length > 2) {
              if (this.multi) throw new I("Graph.".concat(e2, ": cannot use a {source,target} combo when asking about an edge's attributes in a MultiGraph since we cannot infer the one you want information about."));
              var o2 = "" + t3, a2 = "" + r2;
              if (r2 = arguments[2], !(i2 = d(this, o2, a2, n2))) throw new F("Graph.".concat(e2, ': could not find an edge for the given path ("').concat(o2, '" - "').concat(a2, '").'));
            } else {
              if ("mixed" !== n2) throw new I("Graph.".concat(e2, ": calling this method with only a key (vs. a source and target) does not make sense since an edge with this key could have the other type."));
              if (t3 = "" + t3, !(i2 = this._edges.get(t3))) throw new F("Graph.".concat(e2, ': could not find the "').concat(t3, '" edge in the graph.'));
            }
            if (!s(r2)) throw new B("Graph.".concat(e2, ": provided attributes are not a plain object."));
            return i2.attributes = r2, this.emit("edgeAttributesUpdated", { key: i2.key, type: "replace", attributes: i2.attributes }), this;
          };
        } }, { name: function(t2) {
          return "merge".concat(t2, "Attributes");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2) {
            var i2;
            if ("mixed" !== this.type && "mixed" !== n2 && n2 !== this.type) throw new I("Graph.".concat(e2, ": cannot find this type of edges in your ").concat(this.type, " graph."));
            if (arguments.length > 2) {
              if (this.multi) throw new I("Graph.".concat(e2, ": cannot use a {source,target} combo when asking about an edge's attributes in a MultiGraph since we cannot infer the one you want information about."));
              var o2 = "" + t3, a2 = "" + r2;
              if (r2 = arguments[2], !(i2 = d(this, o2, a2, n2))) throw new F("Graph.".concat(e2, ': could not find an edge for the given path ("').concat(o2, '" - "').concat(a2, '").'));
            } else {
              if ("mixed" !== n2) throw new I("Graph.".concat(e2, ": calling this method with only a key (vs. a source and target) does not make sense since an edge with this key could have the other type."));
              if (t3 = "" + t3, !(i2 = this._edges.get(t3))) throw new F("Graph.".concat(e2, ': could not find the "').concat(t3, '" edge in the graph.'));
            }
            if (!s(r2)) throw new B("Graph.".concat(e2, ": provided attributes are not a plain object."));
            return u(i2.attributes, r2), this.emit("edgeAttributesUpdated", { key: i2.key, type: "merge", attributes: i2.attributes, data: r2 }), this;
          };
        } }, { name: function(t2) {
          return "update".concat(t2, "Attributes");
        }, attacher: function(t2, e2, n2) {
          t2.prototype[e2] = function(t3, r2) {
            var i2;
            if ("mixed" !== this.type && "mixed" !== n2 && n2 !== this.type) throw new I("Graph.".concat(e2, ": cannot find this type of edges in your ").concat(this.type, " graph."));
            if (arguments.length > 2) {
              if (this.multi) throw new I("Graph.".concat(e2, ": cannot use a {source,target} combo when asking about an edge's attributes in a MultiGraph since we cannot infer the one you want information about."));
              var o2 = "" + t3, a2 = "" + r2;
              if (r2 = arguments[2], !(i2 = d(this, o2, a2, n2))) throw new F("Graph.".concat(e2, ': could not find an edge for the given path ("').concat(o2, '" - "').concat(a2, '").'));
            } else {
              if ("mixed" !== n2) throw new I("Graph.".concat(e2, ": calling this method with only a key (vs. a source and target) does not make sense since an edge with this key could have the other type."));
              if (t3 = "" + t3, !(i2 = this._edges.get(t3))) throw new F("Graph.".concat(e2, ': could not find the "').concat(t3, '" edge in the graph.'));
            }
            if ("function" != typeof r2) throw new B("Graph.".concat(e2, ": provided updater is not a function."));
            return i2.attributes = r2(i2.attributes), this.emit("edgeAttributesUpdated", { key: i2.key, type: "update", attributes: i2.attributes }), this;
          };
        } }];
        var Z = O, $ = P, tt = function() {
          var t2 = arguments, e2 = null, n2 = -1;
          return new Z((function() {
            for (var r2 = null; ; ) {
              if (null === e2) {
                if (++n2 >= t2.length) return { done: true };
                e2 = $(t2[n2]);
              }
              if (true !== (r2 = e2.next()).done) break;
              e2 = null;
            }
            return r2;
          }));
        }, et = [{ name: "edges", type: "mixed" }, { name: "inEdges", type: "directed", direction: "in" }, { name: "outEdges", type: "directed", direction: "out" }, { name: "inboundEdges", type: "mixed", direction: "in" }, { name: "outboundEdges", type: "mixed", direction: "out" }, { name: "directedEdges", type: "directed" }, { name: "undirectedEdges", type: "undirected" }];
        function nt(t2, e2, n2, r2) {
          var i2 = false;
          for (var o2 in e2) if (o2 !== r2) {
            var a2 = e2[o2];
            if (i2 = n2(a2.key, a2.attributes, a2.source.key, a2.target.key, a2.source.attributes, a2.target.attributes, a2.undirected), t2 && i2) return a2.key;
          }
        }
        function rt(t2, e2, n2, r2) {
          var i2, o2, a2, c2 = false;
          for (var u2 in e2) if (u2 !== r2) {
            i2 = e2[u2];
            do {
              if (o2 = i2.source, a2 = i2.target, c2 = n2(i2.key, i2.attributes, o2.key, a2.key, o2.attributes, a2.attributes, i2.undirected), t2 && c2) return i2.key;
              i2 = i2.next;
            } while (void 0 !== i2);
          }
        }
        function it(t2, e2) {
          var n2, r2 = Object.keys(t2), i2 = r2.length, o2 = 0;
          return new O((function() {
            do {
              if (n2) n2 = n2.next;
              else {
                if (o2 >= i2) return { done: true };
                var a2 = r2[o2++];
                if (a2 === e2) {
                  n2 = void 0;
                  continue;
                }
                n2 = t2[a2];
              }
            } while (!n2);
            return { done: false, value: { edge: n2.key, attributes: n2.attributes, source: n2.source.key, target: n2.target.key, sourceAttributes: n2.source.attributes, targetAttributes: n2.target.attributes, undirected: n2.undirected } };
          }));
        }
        function ot(t2, e2, n2, r2) {
          var i2 = e2[n2];
          if (i2) {
            var o2 = i2.source, a2 = i2.target;
            return r2(i2.key, i2.attributes, o2.key, a2.key, o2.attributes, a2.attributes, i2.undirected) && t2 ? i2.key : void 0;
          }
        }
        function at(t2, e2, n2, r2) {
          var i2 = e2[n2];
          if (i2) {
            var o2 = false;
            do {
              if (o2 = r2(i2.key, i2.attributes, i2.source.key, i2.target.key, i2.source.attributes, i2.target.attributes, i2.undirected), t2 && o2) return i2.key;
              i2 = i2.next;
            } while (void 0 !== i2);
          }
        }
        function ct(t2, e2) {
          var n2 = t2[e2];
          return void 0 !== n2.next ? new O((function() {
            if (!n2) return { done: true };
            var t3 = { edge: n2.key, attributes: n2.attributes, source: n2.source.key, target: n2.target.key, sourceAttributes: n2.source.attributes, targetAttributes: n2.target.attributes, undirected: n2.undirected };
            return n2 = n2.next, { done: false, value: t3 };
          })) : O.of({ edge: n2.key, attributes: n2.attributes, source: n2.source.key, target: n2.target.key, sourceAttributes: n2.source.attributes, targetAttributes: n2.target.attributes, undirected: n2.undirected });
        }
        function ut(t2, e2) {
          if (0 === t2.size) return [];
          if ("mixed" === e2 || e2 === t2.type) return "function" == typeof Array.from ? Array.from(t2._edges.keys()) : K(t2._edges.keys(), t2._edges.size);
          for (var n2, r2, i2 = "undirected" === e2 ? t2.undirectedSize : t2.directedSize, o2 = new Array(i2), a2 = "undirected" === e2, c2 = t2._edges.values(), u2 = 0; true !== (n2 = c2.next()).done; ) (r2 = n2.value).undirected === a2 && (o2[u2++] = r2.key);
          return o2;
        }
        function dt(t2, e2, n2, r2) {
          if (0 !== e2.size) {
            for (var i2, o2, a2 = "mixed" !== n2 && n2 !== e2.type, c2 = "undirected" === n2, u2 = false, d2 = e2._edges.values(); true !== (i2 = d2.next()).done; ) if (o2 = i2.value, !a2 || o2.undirected === c2) {
              var s2 = o2, h2 = s2.key, p2 = s2.attributes, f2 = s2.source, l2 = s2.target;
              if (u2 = r2(h2, p2, f2.key, l2.key, f2.attributes, l2.attributes, o2.undirected), t2 && u2) return h2;
            }
          }
        }
        function st(t2, e2) {
          if (0 === t2.size) return O.empty();
          var n2 = "mixed" !== e2 && e2 !== t2.type, r2 = "undirected" === e2, i2 = t2._edges.values();
          return new O((function() {
            for (var t3, e3; ; ) {
              if ((t3 = i2.next()).done) return t3;
              if (e3 = t3.value, !n2 || e3.undirected === r2) break;
            }
            return { value: { edge: e3.key, attributes: e3.attributes, source: e3.source.key, target: e3.target.key, sourceAttributes: e3.source.attributes, targetAttributes: e3.target.attributes, undirected: e3.undirected }, done: false };
          }));
        }
        function ht(t2, e2, n2, r2, i2, o2) {
          var a2, c2 = e2 ? rt : nt;
          if ("undirected" !== n2) {
            if ("out" !== r2 && (a2 = c2(t2, i2.in, o2), t2 && a2)) return a2;
            if ("in" !== r2 && (a2 = c2(t2, i2.out, o2, r2 ? void 0 : i2.key), t2 && a2)) return a2;
          }
          if ("directed" !== n2 && (a2 = c2(t2, i2.undirected, o2), t2 && a2)) return a2;
        }
        function pt(t2, e2, n2, r2) {
          var i2 = [];
          return ht(false, t2, e2, n2, r2, (function(t3) {
            i2.push(t3);
          })), i2;
        }
        function ft(t2, e2, n2) {
          var r2 = O.empty();
          return "undirected" !== t2 && ("out" !== e2 && void 0 !== n2.in && (r2 = tt(r2, it(n2.in))), "in" !== e2 && void 0 !== n2.out && (r2 = tt(r2, it(n2.out, e2 ? void 0 : n2.key)))), "directed" !== t2 && void 0 !== n2.undirected && (r2 = tt(r2, it(n2.undirected))), r2;
        }
        function lt(t2, e2, n2, r2, i2, o2, a2) {
          var c2, u2 = n2 ? at : ot;
          if ("undirected" !== e2) {
            if (void 0 !== i2.in && "out" !== r2 && (c2 = u2(t2, i2.in, o2, a2), t2 && c2)) return c2;
            if (void 0 !== i2.out && "in" !== r2 && (r2 || i2.key !== o2) && (c2 = u2(t2, i2.out, o2, a2), t2 && c2)) return c2;
          }
          if ("directed" !== e2 && void 0 !== i2.undirected && (c2 = u2(t2, i2.undirected, o2, a2), t2 && c2)) return c2;
        }
        function gt(t2, e2, n2, r2, i2) {
          var o2 = [];
          return lt(false, t2, e2, n2, r2, i2, (function(t3) {
            o2.push(t3);
          })), o2;
        }
        function yt(t2, e2, n2, r2) {
          var i2 = O.empty();
          return "undirected" !== t2 && (void 0 !== n2.in && "out" !== e2 && r2 in n2.in && (i2 = tt(i2, ct(n2.in, r2))), void 0 !== n2.out && "in" !== e2 && r2 in n2.out && (e2 || n2.key !== r2) && (i2 = tt(i2, ct(n2.out, r2)))), "directed" !== t2 && void 0 !== n2.undirected && r2 in n2.undirected && (i2 = tt(i2, ct(n2.undirected, r2))), i2;
        }
        var wt = [{ name: "neighbors", type: "mixed" }, { name: "inNeighbors", type: "directed", direction: "in" }, { name: "outNeighbors", type: "directed", direction: "out" }, { name: "inboundNeighbors", type: "mixed", direction: "in" }, { name: "outboundNeighbors", type: "mixed", direction: "out" }, { name: "directedNeighbors", type: "directed" }, { name: "undirectedNeighbors", type: "undirected" }];
        function vt() {
          this.A = null, this.B = null;
        }
        function bt(t2, e2, n2, r2, i2) {
          for (var o2 in r2) {
            var a2 = r2[o2], c2 = a2.source, u2 = a2.target, d2 = c2 === n2 ? u2 : c2;
            if (!e2 || !e2.has(d2.key)) {
              var s2 = i2(d2.key, d2.attributes);
              if (t2 && s2) return d2.key;
            }
          }
        }
        function mt(t2, e2, n2, r2, i2) {
          if ("mixed" !== e2) {
            if ("undirected" === e2) return bt(t2, null, r2, r2.undirected, i2);
            if ("string" == typeof n2) return bt(t2, null, r2, r2[n2], i2);
          }
          var o2, a2 = new vt();
          if ("undirected" !== e2) {
            if ("out" !== n2) {
              if (o2 = bt(t2, null, r2, r2.in, i2), t2 && o2) return o2;
              a2.wrap(r2.in);
            }
            if ("in" !== n2) {
              if (o2 = bt(t2, a2, r2, r2.out, i2), t2 && o2) return o2;
              a2.wrap(r2.out);
            }
          }
          if ("directed" !== e2 && (o2 = bt(t2, a2, r2, r2.undirected, i2), t2 && o2)) return o2;
        }
        function kt(t2, e2, n2) {
          var r2 = Object.keys(n2), i2 = r2.length, o2 = 0;
          return new O((function() {
            var a2 = null;
            do {
              if (o2 >= i2) return t2 && t2.wrap(n2), { done: true };
              var c2 = n2[r2[o2++]], u2 = c2.source, d2 = c2.target;
              a2 = u2 === e2 ? d2 : u2, t2 && t2.has(a2.key) && (a2 = null);
            } while (null === a2);
            return { done: false, value: { neighbor: a2.key, attributes: a2.attributes } };
          }));
        }
        function _t(t2, e2) {
          var n2 = e2.name, r2 = e2.type, i2 = e2.direction;
          t2.prototype[n2] = function(t3) {
            if ("mixed" !== r2 && "mixed" !== this.type && r2 !== this.type) return [];
            t3 = "" + t3;
            var e3 = this._nodes.get(t3);
            if (void 0 === e3) throw new F("Graph.".concat(n2, ': could not find the "').concat(t3, '" node in the graph.'));
            return (function(t4, e4, n3) {
              if ("mixed" !== t4) {
                if ("undirected" === t4) return Object.keys(n3.undirected);
                if ("string" == typeof e4) return Object.keys(n3[e4]);
              }
              var r3 = [];
              return mt(false, t4, e4, n3, (function(t5) {
                r3.push(t5);
              })), r3;
            })("mixed" === r2 ? this.type : r2, i2, e3);
          };
        }
        function Gt(t2, e2) {
          var n2 = e2.name, r2 = e2.type, i2 = e2.direction, o2 = n2.slice(0, -1) + "Entries";
          t2.prototype[o2] = function(t3) {
            if ("mixed" !== r2 && "mixed" !== this.type && r2 !== this.type) return O.empty();
            t3 = "" + t3;
            var e3 = this._nodes.get(t3);
            if (void 0 === e3) throw new F("Graph.".concat(o2, ': could not find the "').concat(t3, '" node in the graph.'));
            return (function(t4, e4, n3) {
              if ("mixed" !== t4) {
                if ("undirected" === t4) return kt(null, n3, n3.undirected);
                if ("string" == typeof e4) return kt(null, n3, n3[e4]);
              }
              var r3 = O.empty(), i3 = new vt();
              return "undirected" !== t4 && ("out" !== e4 && (r3 = tt(r3, kt(i3, n3, n3.in))), "in" !== e4 && (r3 = tt(r3, kt(i3, n3, n3.out)))), "directed" !== t4 && (r3 = tt(r3, kt(i3, n3, n3.undirected))), r3;
            })("mixed" === r2 ? this.type : r2, i2, e3);
          };
        }
        function xt(t2, e2, n2, r2, i2) {
          for (var o2, a2, c2, u2, d2, s2, h2, p2 = r2._nodes.values(), f2 = r2.type; true !== (o2 = p2.next()).done; ) {
            var l2 = false;
            if (a2 = o2.value, "undirected" !== f2) for (c2 in u2 = a2.out) {
              d2 = u2[c2];
              do {
                if (s2 = d2.target, l2 = true, h2 = i2(a2.key, s2.key, a2.attributes, s2.attributes, d2.key, d2.attributes, d2.undirected), t2 && h2) return d2;
                d2 = d2.next;
              } while (d2);
            }
            if ("directed" !== f2) {
              for (c2 in u2 = a2.undirected) if (!(e2 && a2.key > c2)) {
                d2 = u2[c2];
                do {
                  if ((s2 = d2.target).key !== c2 && (s2 = d2.source), l2 = true, h2 = i2(a2.key, s2.key, a2.attributes, s2.attributes, d2.key, d2.attributes, d2.undirected), t2 && h2) return d2;
                  d2 = d2.next;
                } while (d2);
              }
            }
            if (n2 && !l2 && (h2 = i2(a2.key, null, a2.attributes, null, null, null, null), t2 && h2)) return null;
          }
        }
        function Et(t2) {
          if (!s(t2)) throw new B('Graph.import: invalid serialized node. A serialized node should be a plain object with at least a "key" property.');
          if (!("key" in t2)) throw new B("Graph.import: serialized node is missing its key.");
          if ("attributes" in t2 && (!s(t2.attributes) || null === t2.attributes)) throw new B("Graph.import: invalid attributes. Attributes should be a plain object, null or omitted.");
        }
        function At(t2) {
          if (!s(t2)) throw new B('Graph.import: invalid serialized edge. A serialized edge should be a plain object with at least a "source" & "target" property.');
          if (!("source" in t2)) throw new B("Graph.import: serialized edge is missing its source.");
          if (!("target" in t2)) throw new B("Graph.import: serialized edge is missing its target.");
          if ("attributes" in t2 && (!s(t2.attributes) || null === t2.attributes)) throw new B("Graph.import: invalid attributes. Attributes should be a plain object, null or omitted.");
          if ("undirected" in t2 && "boolean" != typeof t2.undirected) throw new B("Graph.import: invalid undirectedness information. Undirected should be boolean or omitted.");
        }
        vt.prototype.wrap = function(t2) {
          null === this.A ? this.A = t2 : null === this.B && (this.B = t2);
        }, vt.prototype.has = function(t2) {
          return null !== this.A && t2 in this.A || null !== this.B && t2 in this.B;
        };
        var Lt, St = (Lt = 255 & Math.floor(256 * Math.random()), function() {
          return Lt++;
        }), Dt = /* @__PURE__ */ new Set(["directed", "undirected", "mixed"]), Ut = /* @__PURE__ */ new Set(["domain", "_events", "_eventsCount", "_maxListeners"]), Nt = { allowSelfLoops: true, multi: false, type: "mixed" };
        function Ot(t2, e2, n2) {
          var r2 = new t2.NodeDataClass(e2, n2);
          return t2._nodes.set(e2, r2), t2.emit("nodeAdded", { key: e2, attributes: n2 }), r2;
        }
        function jt(t2, e2, n2, r2, i2, o2, a2, c2) {
          if (!r2 && "undirected" === t2.type) throw new I("Graph.".concat(e2, ": you cannot add a directed edge to an undirected graph. Use the #.addEdge or #.addUndirectedEdge instead."));
          if (r2 && "directed" === t2.type) throw new I("Graph.".concat(e2, ": you cannot add an undirected edge to a directed graph. Use the #.addEdge or #.addDirectedEdge instead."));
          if (c2 && !s(c2)) throw new B("Graph.".concat(e2, ': invalid attributes. Expecting an object but got "').concat(c2, '"'));
          if (o2 = "" + o2, a2 = "" + a2, c2 = c2 || {}, !t2.allowSelfLoops && o2 === a2) throw new I("Graph.".concat(e2, ': source & target are the same ("').concat(o2, `"), thus creating a loop explicitly forbidden by this graph 'allowSelfLoops' option set to false.`));
          var u2 = t2._nodes.get(o2), d2 = t2._nodes.get(a2);
          if (!u2) throw new F("Graph.".concat(e2, ': source node "').concat(o2, '" not found.'));
          if (!d2) throw new F("Graph.".concat(e2, ': target node "').concat(a2, '" not found.'));
          var h2 = { key: null, undirected: r2, source: o2, target: a2, attributes: c2 };
          if (n2) i2 = t2._edgeKeyGenerator();
          else if (i2 = "" + i2, t2._edges.has(i2)) throw new I("Graph.".concat(e2, ': the "').concat(i2, '" edge already exists in the graph.'));
          if (!t2.multi && (r2 ? void 0 !== u2.undirected[a2] : void 0 !== u2.out[a2])) throw new I("Graph.".concat(e2, ': an edge linking "').concat(o2, '" to "').concat(a2, `" already exists. If you really want to add multiple edges linking those nodes, you should create a multi graph by using the 'multi' option.`));
          var p2 = new V(r2, i2, u2, d2, c2);
          t2._edges.set(i2, p2);
          var f2 = o2 === a2;
          return r2 ? (u2.undirectedDegree++, d2.undirectedDegree++, f2 && (u2.undirectedLoops++, t2._undirectedSelfLoopCount++)) : (u2.outDegree++, d2.inDegree++, f2 && (u2.directedLoops++, t2._directedSelfLoopCount++)), t2.multi ? p2.attachMulti() : p2.attach(), r2 ? t2._undirectedSize++ : t2._directedSize++, h2.key = i2, t2.emit("edgeAdded", h2), i2;
        }
        function Ct(t2, e2, n2, r2, i2, o2, a2, c2, d2) {
          if (!r2 && "undirected" === t2.type) throw new I("Graph.".concat(e2, ": you cannot merge/update a directed edge to an undirected graph. Use the #.mergeEdge/#.updateEdge or #.addUndirectedEdge instead."));
          if (r2 && "directed" === t2.type) throw new I("Graph.".concat(e2, ": you cannot merge/update an undirected edge to a directed graph. Use the #.mergeEdge/#.updateEdge or #.addDirectedEdge instead."));
          if (c2) {
            if (d2) {
              if ("function" != typeof c2) throw new B("Graph.".concat(e2, ': invalid updater function. Expecting a function but got "').concat(c2, '"'));
            } else if (!s(c2)) throw new B("Graph.".concat(e2, ': invalid attributes. Expecting an object but got "').concat(c2, '"'));
          }
          var h2;
          if (o2 = "" + o2, a2 = "" + a2, d2 && (h2 = c2, c2 = void 0), !t2.allowSelfLoops && o2 === a2) throw new I("Graph.".concat(e2, ': source & target are the same ("').concat(o2, `"), thus creating a loop explicitly forbidden by this graph 'allowSelfLoops' option set to false.`));
          var p2, f2, l2 = t2._nodes.get(o2), g2 = t2._nodes.get(a2);
          if (!n2 && (p2 = t2._edges.get(i2))) {
            if (!(p2.source.key === o2 && p2.target.key === a2 || r2 && p2.source.key === a2 && p2.target.key === o2)) throw new I("Graph.".concat(e2, ': inconsistency detected when attempting to merge the "').concat(i2, '" edge with "').concat(o2, '" source & "').concat(a2, '" target vs. ("').concat(p2.source.key, '", "').concat(p2.target.key, '").'));
            f2 = p2;
          }
          if (f2 || t2.multi || !l2 || (f2 = r2 ? l2.undirected[a2] : l2.out[a2]), f2) {
            var y2 = [f2.key, false, false, false];
            if (d2 ? !h2 : !c2) return y2;
            if (d2) {
              var w2 = f2.attributes;
              f2.attributes = h2(w2), t2.emit("edgeAttributesUpdated", { type: "replace", key: f2.key, attributes: f2.attributes });
            } else u(f2.attributes, c2), t2.emit("edgeAttributesUpdated", { type: "merge", key: f2.key, attributes: f2.attributes, data: c2 });
            return y2;
          }
          c2 = c2 || {}, d2 && h2 && (c2 = h2(c2));
          var v2 = { key: null, undirected: r2, source: o2, target: a2, attributes: c2 };
          if (n2) i2 = t2._edgeKeyGenerator();
          else if (i2 = "" + i2, t2._edges.has(i2)) throw new I("Graph.".concat(e2, ': the "').concat(i2, '" edge already exists in the graph.'));
          var b2 = false, m2 = false;
          l2 || (l2 = Ot(t2, o2, {}), b2 = true, o2 === a2 && (g2 = l2, m2 = true)), g2 || (g2 = Ot(t2, a2, {}), m2 = true), p2 = new V(r2, i2, l2, g2, c2), t2._edges.set(i2, p2);
          var k2 = o2 === a2;
          return r2 ? (l2.undirectedDegree++, g2.undirectedDegree++, k2 && (l2.undirectedLoops++, t2._undirectedSelfLoopCount++)) : (l2.outDegree++, g2.inDegree++, k2 && (l2.directedLoops++, t2._directedSelfLoopCount++)), t2.multi ? p2.attachMulti() : p2.attach(), r2 ? t2._undirectedSize++ : t2._directedSize++, v2.key = i2, t2.emit("edgeAdded", v2), [i2, true, b2, m2];
        }
        function Mt(t2, e2) {
          t2._edges.delete(e2.key);
          var n2 = e2.source, r2 = e2.target, i2 = e2.attributes, o2 = e2.undirected, a2 = n2 === r2;
          o2 ? (n2.undirectedDegree--, r2.undirectedDegree--, a2 && (n2.undirectedLoops--, t2._undirectedSelfLoopCount--)) : (n2.outDegree--, r2.inDegree--, a2 && (n2.directedLoops--, t2._directedSelfLoopCount--)), t2.multi ? e2.detachMulti() : e2.detach(), o2 ? t2._undirectedSize-- : t2._directedSize--, t2.emit("edgeDropped", { key: e2.key, attributes: i2, source: n2.key, target: r2.key, undirected: o2 });
        }
        var zt = (function(n2) {
          function r2(t2) {
            var e2;
            if (e2 = n2.call(this) || this, "boolean" != typeof (t2 = u({}, Nt, t2)).multi) throw new B(`Graph.constructor: invalid 'multi' option. Expecting a boolean but got "`.concat(t2.multi, '".'));
            if (!Dt.has(t2.type)) throw new B(`Graph.constructor: invalid 'type' option. Should be one of "mixed", "directed" or "undirected" but got "`.concat(t2.type, '".'));
            if ("boolean" != typeof t2.allowSelfLoops) throw new B(`Graph.constructor: invalid 'allowSelfLoops' option. Expecting a boolean but got "`.concat(t2.allowSelfLoops, '".'));
            var r3 = "mixed" === t2.type ? Y : "directed" === t2.type ? q : J;
            p(c(e2), "NodeDataClass", r3);
            var i3 = "geid_" + St() + "_", o2 = 0;
            return p(c(e2), "_attributes", {}), p(c(e2), "_nodes", /* @__PURE__ */ new Map()), p(c(e2), "_edges", /* @__PURE__ */ new Map()), p(c(e2), "_directedSize", 0), p(c(e2), "_undirectedSize", 0), p(c(e2), "_directedSelfLoopCount", 0), p(c(e2), "_undirectedSelfLoopCount", 0), p(c(e2), "_edgeKeyGenerator", (function() {
              var t3;
              do {
                t3 = i3 + o2++;
              } while (e2._edges.has(t3));
              return t3;
            })), p(c(e2), "_options", t2), Ut.forEach((function(t3) {
              return p(c(e2), t3, e2[t3]);
            })), f(c(e2), "order", (function() {
              return e2._nodes.size;
            })), f(c(e2), "size", (function() {
              return e2._edges.size;
            })), f(c(e2), "directedSize", (function() {
              return e2._directedSize;
            })), f(c(e2), "undirectedSize", (function() {
              return e2._undirectedSize;
            })), f(c(e2), "selfLoopCount", (function() {
              return e2._directedSelfLoopCount + e2._undirectedSelfLoopCount;
            })), f(c(e2), "directedSelfLoopCount", (function() {
              return e2._directedSelfLoopCount;
            })), f(c(e2), "undirectedSelfLoopCount", (function() {
              return e2._undirectedSelfLoopCount;
            })), f(c(e2), "multi", e2._options.multi), f(c(e2), "type", e2._options.type), f(c(e2), "allowSelfLoops", e2._options.allowSelfLoops), f(c(e2), "implementation", (function() {
              return "graphology";
            })), e2;
          }
          e(r2, n2);
          var i2 = r2.prototype;
          return i2._resetInstanceCounters = function() {
            this._directedSize = 0, this._undirectedSize = 0, this._directedSelfLoopCount = 0, this._undirectedSelfLoopCount = 0;
          }, i2.hasNode = function(t2) {
            return this._nodes.has("" + t2);
          }, i2.hasDirectedEdge = function(t2, e2) {
            if ("undirected" === this.type) return false;
            if (1 === arguments.length) {
              var n3 = "" + t2, r3 = this._edges.get(n3);
              return !!r3 && !r3.undirected;
            }
            if (2 === arguments.length) {
              t2 = "" + t2, e2 = "" + e2;
              var i3 = this._nodes.get(t2);
              return !!i3 && i3.out.hasOwnProperty(e2);
            }
            throw new B("Graph.hasDirectedEdge: invalid arity (".concat(arguments.length, ", instead of 1 or 2). You can either ask for an edge id or for the existence of an edge between a source & a target."));
          }, i2.hasUndirectedEdge = function(t2, e2) {
            if ("directed" === this.type) return false;
            if (1 === arguments.length) {
              var n3 = "" + t2, r3 = this._edges.get(n3);
              return !!r3 && r3.undirected;
            }
            if (2 === arguments.length) {
              t2 = "" + t2, e2 = "" + e2;
              var i3 = this._nodes.get(t2);
              return !!i3 && i3.undirected.hasOwnProperty(e2);
            }
            throw new B("Graph.hasDirectedEdge: invalid arity (".concat(arguments.length, ", instead of 1 or 2). You can either ask for an edge id or for the existence of an edge between a source & a target."));
          }, i2.hasEdge = function(t2, e2) {
            if (1 === arguments.length) {
              var n3 = "" + t2;
              return this._edges.has(n3);
            }
            if (2 === arguments.length) {
              t2 = "" + t2, e2 = "" + e2;
              var r3 = this._nodes.get(t2);
              return !!r3 && (void 0 !== r3.out && r3.out.hasOwnProperty(e2) || void 0 !== r3.undirected && r3.undirected.hasOwnProperty(e2));
            }
            throw new B("Graph.hasEdge: invalid arity (".concat(arguments.length, ", instead of 1 or 2). You can either ask for an edge id or for the existence of an edge between a source & a target."));
          }, i2.directedEdge = function(t2, e2) {
            if ("undirected" !== this.type) {
              if (t2 = "" + t2, e2 = "" + e2, this.multi) throw new I("Graph.directedEdge: this method is irrelevant with multigraphs since there might be multiple edges between source & target. See #.directedEdges instead.");
              var n3 = this._nodes.get(t2);
              if (!n3) throw new F('Graph.directedEdge: could not find the "'.concat(t2, '" source node in the graph.'));
              if (!this._nodes.has(e2)) throw new F('Graph.directedEdge: could not find the "'.concat(e2, '" target node in the graph.'));
              var r3 = n3.out && n3.out[e2] || void 0;
              return r3 ? r3.key : void 0;
            }
          }, i2.undirectedEdge = function(t2, e2) {
            if ("directed" !== this.type) {
              if (t2 = "" + t2, e2 = "" + e2, this.multi) throw new I("Graph.undirectedEdge: this method is irrelevant with multigraphs since there might be multiple edges between source & target. See #.undirectedEdges instead.");
              var n3 = this._nodes.get(t2);
              if (!n3) throw new F('Graph.undirectedEdge: could not find the "'.concat(t2, '" source node in the graph.'));
              if (!this._nodes.has(e2)) throw new F('Graph.undirectedEdge: could not find the "'.concat(e2, '" target node in the graph.'));
              var r3 = n3.undirected && n3.undirected[e2] || void 0;
              return r3 ? r3.key : void 0;
            }
          }, i2.edge = function(t2, e2) {
            if (this.multi) throw new I("Graph.edge: this method is irrelevant with multigraphs since there might be multiple edges between source & target. See #.edges instead.");
            t2 = "" + t2, e2 = "" + e2;
            var n3 = this._nodes.get(t2);
            if (!n3) throw new F('Graph.edge: could not find the "'.concat(t2, '" source node in the graph.'));
            if (!this._nodes.has(e2)) throw new F('Graph.edge: could not find the "'.concat(e2, '" target node in the graph.'));
            var r3 = n3.out && n3.out[e2] || n3.undirected && n3.undirected[e2] || void 0;
            if (r3) return r3.key;
          }, i2.areDirectedNeighbors = function(t2, e2) {
            t2 = "" + t2, e2 = "" + e2;
            var n3 = this._nodes.get(t2);
            if (!n3) throw new F('Graph.areDirectedNeighbors: could not find the "'.concat(t2, '" node in the graph.'));
            return "undirected" !== this.type && (e2 in n3.in || e2 in n3.out);
          }, i2.areOutNeighbors = function(t2, e2) {
            t2 = "" + t2, e2 = "" + e2;
            var n3 = this._nodes.get(t2);
            if (!n3) throw new F('Graph.areOutNeighbors: could not find the "'.concat(t2, '" node in the graph.'));
            return "undirected" !== this.type && e2 in n3.out;
          }, i2.areInNeighbors = function(t2, e2) {
            t2 = "" + t2, e2 = "" + e2;
            var n3 = this._nodes.get(t2);
            if (!n3) throw new F('Graph.areInNeighbors: could not find the "'.concat(t2, '" node in the graph.'));
            return "undirected" !== this.type && e2 in n3.in;
          }, i2.areUndirectedNeighbors = function(t2, e2) {
            t2 = "" + t2, e2 = "" + e2;
            var n3 = this._nodes.get(t2);
            if (!n3) throw new F('Graph.areUndirectedNeighbors: could not find the "'.concat(t2, '" node in the graph.'));
            return "directed" !== this.type && e2 in n3.undirected;
          }, i2.areNeighbors = function(t2, e2) {
            t2 = "" + t2, e2 = "" + e2;
            var n3 = this._nodes.get(t2);
            if (!n3) throw new F('Graph.areNeighbors: could not find the "'.concat(t2, '" node in the graph.'));
            return "undirected" !== this.type && (e2 in n3.in || e2 in n3.out) || "directed" !== this.type && e2 in n3.undirected;
          }, i2.areInboundNeighbors = function(t2, e2) {
            t2 = "" + t2, e2 = "" + e2;
            var n3 = this._nodes.get(t2);
            if (!n3) throw new F('Graph.areInboundNeighbors: could not find the "'.concat(t2, '" node in the graph.'));
            return "undirected" !== this.type && e2 in n3.in || "directed" !== this.type && e2 in n3.undirected;
          }, i2.areOutboundNeighbors = function(t2, e2) {
            t2 = "" + t2, e2 = "" + e2;
            var n3 = this._nodes.get(t2);
            if (!n3) throw new F('Graph.areOutboundNeighbors: could not find the "'.concat(t2, '" node in the graph.'));
            return "undirected" !== this.type && e2 in n3.out || "directed" !== this.type && e2 in n3.undirected;
          }, i2.inDegree = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.inDegree: could not find the "'.concat(t2, '" node in the graph.'));
            return "undirected" === this.type ? 0 : e2.inDegree;
          }, i2.outDegree = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.outDegree: could not find the "'.concat(t2, '" node in the graph.'));
            return "undirected" === this.type ? 0 : e2.outDegree;
          }, i2.directedDegree = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.directedDegree: could not find the "'.concat(t2, '" node in the graph.'));
            return "undirected" === this.type ? 0 : e2.inDegree + e2.outDegree;
          }, i2.undirectedDegree = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.undirectedDegree: could not find the "'.concat(t2, '" node in the graph.'));
            return "directed" === this.type ? 0 : e2.undirectedDegree;
          }, i2.inboundDegree = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.inboundDegree: could not find the "'.concat(t2, '" node in the graph.'));
            var n3 = 0;
            return "directed" !== this.type && (n3 += e2.undirectedDegree), "undirected" !== this.type && (n3 += e2.inDegree), n3;
          }, i2.outboundDegree = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.outboundDegree: could not find the "'.concat(t2, '" node in the graph.'));
            var n3 = 0;
            return "directed" !== this.type && (n3 += e2.undirectedDegree), "undirected" !== this.type && (n3 += e2.outDegree), n3;
          }, i2.degree = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.degree: could not find the "'.concat(t2, '" node in the graph.'));
            var n3 = 0;
            return "directed" !== this.type && (n3 += e2.undirectedDegree), "undirected" !== this.type && (n3 += e2.inDegree + e2.outDegree), n3;
          }, i2.inDegreeWithoutSelfLoops = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.inDegreeWithoutSelfLoops: could not find the "'.concat(t2, '" node in the graph.'));
            return "undirected" === this.type ? 0 : e2.inDegree - e2.directedLoops;
          }, i2.outDegreeWithoutSelfLoops = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.outDegreeWithoutSelfLoops: could not find the "'.concat(t2, '" node in the graph.'));
            return "undirected" === this.type ? 0 : e2.outDegree - e2.directedLoops;
          }, i2.directedDegreeWithoutSelfLoops = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.directedDegreeWithoutSelfLoops: could not find the "'.concat(t2, '" node in the graph.'));
            return "undirected" === this.type ? 0 : e2.inDegree + e2.outDegree - 2 * e2.directedLoops;
          }, i2.undirectedDegreeWithoutSelfLoops = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.undirectedDegreeWithoutSelfLoops: could not find the "'.concat(t2, '" node in the graph.'));
            return "directed" === this.type ? 0 : e2.undirectedDegree - 2 * e2.undirectedLoops;
          }, i2.inboundDegreeWithoutSelfLoops = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.inboundDegreeWithoutSelfLoops: could not find the "'.concat(t2, '" node in the graph.'));
            var n3 = 0, r3 = 0;
            return "directed" !== this.type && (n3 += e2.undirectedDegree, r3 += 2 * e2.undirectedLoops), "undirected" !== this.type && (n3 += e2.inDegree, r3 += e2.directedLoops), n3 - r3;
          }, i2.outboundDegreeWithoutSelfLoops = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.outboundDegreeWithoutSelfLoops: could not find the "'.concat(t2, '" node in the graph.'));
            var n3 = 0, r3 = 0;
            return "directed" !== this.type && (n3 += e2.undirectedDegree, r3 += 2 * e2.undirectedLoops), "undirected" !== this.type && (n3 += e2.outDegree, r3 += e2.directedLoops), n3 - r3;
          }, i2.degreeWithoutSelfLoops = function(t2) {
            t2 = "" + t2;
            var e2 = this._nodes.get(t2);
            if (!e2) throw new F('Graph.degreeWithoutSelfLoops: could not find the "'.concat(t2, '" node in the graph.'));
            var n3 = 0, r3 = 0;
            return "directed" !== this.type && (n3 += e2.undirectedDegree, r3 += 2 * e2.undirectedLoops), "undirected" !== this.type && (n3 += e2.inDegree + e2.outDegree, r3 += 2 * e2.directedLoops), n3 - r3;
          }, i2.source = function(t2) {
            t2 = "" + t2;
            var e2 = this._edges.get(t2);
            if (!e2) throw new F('Graph.source: could not find the "'.concat(t2, '" edge in the graph.'));
            return e2.source.key;
          }, i2.target = function(t2) {
            t2 = "" + t2;
            var e2 = this._edges.get(t2);
            if (!e2) throw new F('Graph.target: could not find the "'.concat(t2, '" edge in the graph.'));
            return e2.target.key;
          }, i2.extremities = function(t2) {
            t2 = "" + t2;
            var e2 = this._edges.get(t2);
            if (!e2) throw new F('Graph.extremities: could not find the "'.concat(t2, '" edge in the graph.'));
            return [e2.source.key, e2.target.key];
          }, i2.opposite = function(t2, e2) {
            t2 = "" + t2, e2 = "" + e2;
            var n3 = this._edges.get(e2);
            if (!n3) throw new F('Graph.opposite: could not find the "'.concat(e2, '" edge in the graph.'));
            var r3 = n3.source.key, i3 = n3.target.key;
            if (t2 === r3) return i3;
            if (t2 === i3) return r3;
            throw new F('Graph.opposite: the "'.concat(t2, '" node is not attached to the "').concat(e2, '" edge (').concat(r3, ", ").concat(i3, ")."));
          }, i2.hasExtremity = function(t2, e2) {
            t2 = "" + t2, e2 = "" + e2;
            var n3 = this._edges.get(t2);
            if (!n3) throw new F('Graph.hasExtremity: could not find the "'.concat(t2, '" edge in the graph.'));
            return n3.source.key === e2 || n3.target.key === e2;
          }, i2.isUndirected = function(t2) {
            t2 = "" + t2;
            var e2 = this._edges.get(t2);
            if (!e2) throw new F('Graph.isUndirected: could not find the "'.concat(t2, '" edge in the graph.'));
            return e2.undirected;
          }, i2.isDirected = function(t2) {
            t2 = "" + t2;
            var e2 = this._edges.get(t2);
            if (!e2) throw new F('Graph.isDirected: could not find the "'.concat(t2, '" edge in the graph.'));
            return !e2.undirected;
          }, i2.isSelfLoop = function(t2) {
            t2 = "" + t2;
            var e2 = this._edges.get(t2);
            if (!e2) throw new F('Graph.isSelfLoop: could not find the "'.concat(t2, '" edge in the graph.'));
            return e2.source === e2.target;
          }, i2.addNode = function(t2, e2) {
            var n3 = (function(t3, e3, n4) {
              if (n4 && !s(n4)) throw new B('Graph.addNode: invalid attributes. Expecting an object but got "'.concat(n4, '"'));
              if (e3 = "" + e3, n4 = n4 || {}, t3._nodes.has(e3)) throw new I('Graph.addNode: the "'.concat(e3, '" node already exist in the graph.'));
              var r3 = new t3.NodeDataClass(e3, n4);
              return t3._nodes.set(e3, r3), t3.emit("nodeAdded", { key: e3, attributes: n4 }), r3;
            })(this, t2, e2);
            return n3.key;
          }, i2.mergeNode = function(t2, e2) {
            if (e2 && !s(e2)) throw new B('Graph.mergeNode: invalid attributes. Expecting an object but got "'.concat(e2, '"'));
            t2 = "" + t2, e2 = e2 || {};
            var n3 = this._nodes.get(t2);
            return n3 ? (e2 && (u(n3.attributes, e2), this.emit("nodeAttributesUpdated", { type: "merge", key: t2, attributes: n3.attributes, data: e2 })), [t2, false]) : (n3 = new this.NodeDataClass(t2, e2), this._nodes.set(t2, n3), this.emit("nodeAdded", { key: t2, attributes: e2 }), [t2, true]);
          }, i2.updateNode = function(t2, e2) {
            if (e2 && "function" != typeof e2) throw new B('Graph.updateNode: invalid updater function. Expecting a function but got "'.concat(e2, '"'));
            t2 = "" + t2;
            var n3 = this._nodes.get(t2);
            if (n3) {
              if (e2) {
                var r3 = n3.attributes;
                n3.attributes = e2(r3), this.emit("nodeAttributesUpdated", { type: "replace", key: t2, attributes: n3.attributes });
              }
              return [t2, false];
            }
            var i3 = e2 ? e2({}) : {};
            return n3 = new this.NodeDataClass(t2, i3), this._nodes.set(t2, n3), this.emit("nodeAdded", { key: t2, attributes: i3 }), [t2, true];
          }, i2.dropNode = function(t2) {
            t2 = "" + t2;
            var e2, n3 = this._nodes.get(t2);
            if (!n3) throw new F('Graph.dropNode: could not find the "'.concat(t2, '" node in the graph.'));
            if ("undirected" !== this.type) {
              for (var r3 in n3.out) {
                e2 = n3.out[r3];
                do {
                  Mt(this, e2), e2 = e2.next;
                } while (e2);
              }
              for (var i3 in n3.in) {
                e2 = n3.in[i3];
                do {
                  Mt(this, e2), e2 = e2.next;
                } while (e2);
              }
            }
            if ("directed" !== this.type) for (var o2 in n3.undirected) {
              e2 = n3.undirected[o2];
              do {
                Mt(this, e2), e2 = e2.next;
              } while (e2);
            }
            this._nodes.delete(t2), this.emit("nodeDropped", { key: t2, attributes: n3.attributes });
          }, i2.dropEdge = function(t2) {
            var e2;
            if (arguments.length > 1) {
              var n3 = "" + arguments[0], r3 = "" + arguments[1];
              if (!(e2 = d(this, n3, r3, this.type))) throw new F('Graph.dropEdge: could not find the "'.concat(n3, '" -> "').concat(r3, '" edge in the graph.'));
            } else if (t2 = "" + t2, !(e2 = this._edges.get(t2))) throw new F('Graph.dropEdge: could not find the "'.concat(t2, '" edge in the graph.'));
            return Mt(this, e2), this;
          }, i2.dropDirectedEdge = function(t2, e2) {
            if (arguments.length < 2) throw new I("Graph.dropDirectedEdge: it does not make sense to try and drop a directed edge by key. What if the edge with this key is undirected? Use #.dropEdge for this purpose instead.");
            if (this.multi) throw new I("Graph.dropDirectedEdge: cannot use a {source,target} combo when dropping an edge in a MultiGraph since we cannot infer the one you want to delete as there could be multiple ones.");
            var n3 = d(this, t2 = "" + t2, e2 = "" + e2, "directed");
            if (!n3) throw new F('Graph.dropDirectedEdge: could not find a "'.concat(t2, '" -> "').concat(e2, '" edge in the graph.'));
            return Mt(this, n3), this;
          }, i2.dropUndirectedEdge = function(t2, e2) {
            if (arguments.length < 2) throw new I("Graph.dropUndirectedEdge: it does not make sense to drop a directed edge by key. What if the edge with this key is undirected? Use #.dropEdge for this purpose instead.");
            if (this.multi) throw new I("Graph.dropUndirectedEdge: cannot use a {source,target} combo when dropping an edge in a MultiGraph since we cannot infer the one you want to delete as there could be multiple ones.");
            var n3 = d(this, t2, e2, "undirected");
            if (!n3) throw new F('Graph.dropUndirectedEdge: could not find a "'.concat(t2, '" -> "').concat(e2, '" edge in the graph.'));
            return Mt(this, n3), this;
          }, i2.clear = function() {
            this._edges.clear(), this._nodes.clear(), this._resetInstanceCounters(), this.emit("cleared");
          }, i2.clearEdges = function() {
            for (var t2, e2 = this._nodes.values(); true !== (t2 = e2.next()).done; ) t2.value.clear();
            this._edges.clear(), this._resetInstanceCounters(), this.emit("edgesCleared");
          }, i2.getAttribute = function(t2) {
            return this._attributes[t2];
          }, i2.getAttributes = function() {
            return this._attributes;
          }, i2.hasAttribute = function(t2) {
            return this._attributes.hasOwnProperty(t2);
          }, i2.setAttribute = function(t2, e2) {
            return this._attributes[t2] = e2, this.emit("attributesUpdated", { type: "set", attributes: this._attributes, name: t2 }), this;
          }, i2.updateAttribute = function(t2, e2) {
            if ("function" != typeof e2) throw new B("Graph.updateAttribute: updater should be a function.");
            var n3 = this._attributes[t2];
            return this._attributes[t2] = e2(n3), this.emit("attributesUpdated", { type: "set", attributes: this._attributes, name: t2 }), this;
          }, i2.removeAttribute = function(t2) {
            return delete this._attributes[t2], this.emit("attributesUpdated", { type: "remove", attributes: this._attributes, name: t2 }), this;
          }, i2.replaceAttributes = function(t2) {
            if (!s(t2)) throw new B("Graph.replaceAttributes: provided attributes are not a plain object.");
            return this._attributes = t2, this.emit("attributesUpdated", { type: "replace", attributes: this._attributes }), this;
          }, i2.mergeAttributes = function(t2) {
            if (!s(t2)) throw new B("Graph.mergeAttributes: provided attributes are not a plain object.");
            return u(this._attributes, t2), this.emit("attributesUpdated", { type: "merge", attributes: this._attributes, data: t2 }), this;
          }, i2.updateAttributes = function(t2) {
            if ("function" != typeof t2) throw new B("Graph.updateAttributes: provided updater is not a function.");
            return this._attributes = t2(this._attributes), this.emit("attributesUpdated", { type: "update", attributes: this._attributes }), this;
          }, i2.updateEachNodeAttributes = function(t2, e2) {
            if ("function" != typeof t2) throw new B("Graph.updateEachNodeAttributes: expecting an updater function.");
            if (e2 && !l(e2)) throw new B("Graph.updateEachNodeAttributes: invalid hints. Expecting an object having the following shape: {attributes?: [string]}");
            for (var n3, r3, i3 = this._nodes.values(); true !== (n3 = i3.next()).done; ) (r3 = n3.value).attributes = t2(r3.key, r3.attributes);
            this.emit("eachNodeAttributesUpdated", { hints: e2 || null });
          }, i2.updateEachEdgeAttributes = function(t2, e2) {
            if ("function" != typeof t2) throw new B("Graph.updateEachEdgeAttributes: expecting an updater function.");
            if (e2 && !l(e2)) throw new B("Graph.updateEachEdgeAttributes: invalid hints. Expecting an object having the following shape: {attributes?: [string]}");
            for (var n3, r3, i3, o2, a2 = this._edges.values(); true !== (n3 = a2.next()).done; ) i3 = (r3 = n3.value).source, o2 = r3.target, r3.attributes = t2(r3.key, r3.attributes, i3.key, o2.key, i3.attributes, o2.attributes, r3.undirected);
            this.emit("eachEdgeAttributesUpdated", { hints: e2 || null });
          }, i2.forEachAdjacencyEntry = function(t2) {
            if ("function" != typeof t2) throw new B("Graph.forEachAdjacencyEntry: expecting a callback.");
            xt(false, false, false, this, t2);
          }, i2.forEachAdjacencyEntryWithOrphans = function(t2) {
            if ("function" != typeof t2) throw new B("Graph.forEachAdjacencyEntryWithOrphans: expecting a callback.");
            xt(false, false, true, this, t2);
          }, i2.forEachAssymetricAdjacencyEntry = function(t2) {
            if ("function" != typeof t2) throw new B("Graph.forEachAssymetricAdjacencyEntry: expecting a callback.");
            xt(false, true, false, this, t2);
          }, i2.forEachAssymetricAdjacencyEntryWithOrphans = function(t2) {
            if ("function" != typeof t2) throw new B("Graph.forEachAssymetricAdjacencyEntryWithOrphans: expecting a callback.");
            xt(false, true, true, this, t2);
          }, i2.nodes = function() {
            return "function" == typeof Array.from ? Array.from(this._nodes.keys()) : K(this._nodes.keys(), this._nodes.size);
          }, i2.forEachNode = function(t2) {
            if ("function" != typeof t2) throw new B("Graph.forEachNode: expecting a callback.");
            for (var e2, n3, r3 = this._nodes.values(); true !== (e2 = r3.next()).done; ) t2((n3 = e2.value).key, n3.attributes);
          }, i2.findNode = function(t2) {
            if ("function" != typeof t2) throw new B("Graph.findNode: expecting a callback.");
            for (var e2, n3, r3 = this._nodes.values(); true !== (e2 = r3.next()).done; ) if (t2((n3 = e2.value).key, n3.attributes)) return n3.key;
          }, i2.mapNodes = function(t2) {
            if ("function" != typeof t2) throw new B("Graph.mapNode: expecting a callback.");
            for (var e2, n3, r3 = this._nodes.values(), i3 = new Array(this.order), o2 = 0; true !== (e2 = r3.next()).done; ) n3 = e2.value, i3[o2++] = t2(n3.key, n3.attributes);
            return i3;
          }, i2.someNode = function(t2) {
            if ("function" != typeof t2) throw new B("Graph.someNode: expecting a callback.");
            for (var e2, n3, r3 = this._nodes.values(); true !== (e2 = r3.next()).done; ) if (t2((n3 = e2.value).key, n3.attributes)) return true;
            return false;
          }, i2.everyNode = function(t2) {
            if ("function" != typeof t2) throw new B("Graph.everyNode: expecting a callback.");
            for (var e2, n3, r3 = this._nodes.values(); true !== (e2 = r3.next()).done; ) if (!t2((n3 = e2.value).key, n3.attributes)) return false;
            return true;
          }, i2.filterNodes = function(t2) {
            if ("function" != typeof t2) throw new B("Graph.filterNodes: expecting a callback.");
            for (var e2, n3, r3 = this._nodes.values(), i3 = []; true !== (e2 = r3.next()).done; ) t2((n3 = e2.value).key, n3.attributes) && i3.push(n3.key);
            return i3;
          }, i2.reduceNodes = function(t2, e2) {
            if ("function" != typeof t2) throw new B("Graph.reduceNodes: expecting a callback.");
            if (arguments.length < 2) throw new B("Graph.reduceNodes: missing initial value. You must provide it because the callback takes more than one argument and we cannot infer the initial value from the first iteration, as you could with a simple array.");
            for (var n3, r3, i3 = e2, o2 = this._nodes.values(); true !== (n3 = o2.next()).done; ) i3 = t2(i3, (r3 = n3.value).key, r3.attributes);
            return i3;
          }, i2.nodeEntries = function() {
            var t2 = this._nodes.values();
            return new O((function() {
              var e2 = t2.next();
              if (e2.done) return e2;
              var n3 = e2.value;
              return { value: { node: n3.key, attributes: n3.attributes }, done: false };
            }));
          }, i2.export = function() {
            var t2 = this, e2 = new Array(this._nodes.size), n3 = 0;
            this._nodes.forEach((function(t3, r4) {
              e2[n3++] = (function(t4, e3) {
                var n4 = { key: t4 };
                return h(e3.attributes) || (n4.attributes = u({}, e3.attributes)), n4;
              })(r4, t3);
            }));
            var r3 = new Array(this._edges.size);
            return n3 = 0, this._edges.forEach((function(e3, i3) {
              r3[n3++] = (function(t3, e4, n4) {
                var r4 = { key: e4, source: n4.source.key, target: n4.target.key };
                return h(n4.attributes) || (r4.attributes = u({}, n4.attributes)), "mixed" === t3 && n4.undirected && (r4.undirected = true), r4;
              })(t2.type, i3, e3);
            })), { options: { type: this.type, multi: this.multi, allowSelfLoops: this.allowSelfLoops }, attributes: this.getAttributes(), nodes: e2, edges: r3 };
          }, i2.import = function(t2) {
            var e2, n3, i3, o2, a2, c2 = this, u2 = arguments.length > 1 && void 0 !== arguments[1] && arguments[1];
            if (t2 instanceof r2) return t2.forEachNode((function(t3, e3) {
              u2 ? c2.mergeNode(t3, e3) : c2.addNode(t3, e3);
            })), t2.forEachEdge((function(t3, e3, n4, r3, i4, o3, a3) {
              u2 ? a3 ? c2.mergeUndirectedEdgeWithKey(t3, n4, r3, e3) : c2.mergeDirectedEdgeWithKey(t3, n4, r3, e3) : a3 ? c2.addUndirectedEdgeWithKey(t3, n4, r3, e3) : c2.addDirectedEdgeWithKey(t3, n4, r3, e3);
            })), this;
            if (!s(t2)) throw new B("Graph.import: invalid argument. Expecting a serialized graph or, alternatively, a Graph instance.");
            if (t2.attributes) {
              if (!s(t2.attributes)) throw new B("Graph.import: invalid attributes. Expecting a plain object.");
              u2 ? this.mergeAttributes(t2.attributes) : this.replaceAttributes(t2.attributes);
            }
            if (t2.nodes) {
              if (i3 = t2.nodes, !Array.isArray(i3)) throw new B("Graph.import: invalid nodes. Expecting an array.");
              for (e2 = 0, n3 = i3.length; e2 < n3; e2++) {
                Et(o2 = i3[e2]);
                var d2 = o2, h2 = d2.key, p2 = d2.attributes;
                u2 ? this.mergeNode(h2, p2) : this.addNode(h2, p2);
              }
            }
            if (t2.edges) {
              var f2 = false;
              if ("undirected" === this.type && (f2 = true), i3 = t2.edges, !Array.isArray(i3)) throw new B("Graph.import: invalid edges. Expecting an array.");
              for (e2 = 0, n3 = i3.length; e2 < n3; e2++) {
                At(a2 = i3[e2]);
                var l2 = a2, g2 = l2.source, y2 = l2.target, w2 = l2.attributes, v2 = l2.undirected, b2 = void 0 === v2 ? f2 : v2;
                "key" in a2 ? (u2 ? b2 ? this.mergeUndirectedEdgeWithKey : this.mergeDirectedEdgeWithKey : b2 ? this.addUndirectedEdgeWithKey : this.addDirectedEdgeWithKey).call(this, a2.key, g2, y2, w2) : (u2 ? b2 ? this.mergeUndirectedEdge : this.mergeDirectedEdge : b2 ? this.addUndirectedEdge : this.addDirectedEdge).call(this, g2, y2, w2);
              }
            }
            return this;
          }, i2.nullCopy = function(t2) {
            var e2 = new r2(u({}, this._options, t2));
            return e2.replaceAttributes(u({}, this.getAttributes())), e2;
          }, i2.emptyCopy = function(t2) {
            var e2 = this.nullCopy(t2);
            return this._nodes.forEach((function(t3, n3) {
              var r3 = u({}, t3.attributes);
              t3 = new e2.NodeDataClass(n3, r3), e2._nodes.set(n3, t3);
            })), e2;
          }, i2.copy = function(t2) {
            if ("string" == typeof (t2 = t2 || {}).type && t2.type !== this.type && "mixed" !== t2.type) throw new I('Graph.copy: cannot create an incompatible copy from "'.concat(this.type, '" type to "').concat(t2.type, '" because this would mean losing information about the current graph.'));
            if ("boolean" == typeof t2.multi && t2.multi !== this.multi && true !== t2.multi) throw new I("Graph.copy: cannot create an incompatible copy by downgrading a multi graph to a simple one because this would mean losing information about the current graph.");
            if ("boolean" == typeof t2.allowSelfLoops && t2.allowSelfLoops !== this.allowSelfLoops && true !== t2.allowSelfLoops) throw new I("Graph.copy: cannot create an incompatible copy from a graph allowing self loops to one that does not because this would mean losing information about the current graph.");
            for (var e2, n3, r3 = this.emptyCopy(t2), i3 = this._edges.values(); true !== (e2 = i3.next()).done; ) jt(r3, "copy", false, (n3 = e2.value).undirected, n3.key, n3.source.key, n3.target.key, u({}, n3.attributes));
            return r3;
          }, i2.toJSON = function() {
            return this.export();
          }, i2.toString = function() {
            return "[object Graph]";
          }, i2.inspect = function() {
            var e2 = this, n3 = {};
            this._nodes.forEach((function(t2, e3) {
              n3[e3] = t2.attributes;
            }));
            var r3 = {}, i3 = {};
            this._edges.forEach((function(t2, n4) {
              var o3, a3 = t2.undirected ? "--" : "->", c2 = "", u2 = t2.source.key, d2 = t2.target.key;
              t2.undirected && u2 > d2 && (o3 = u2, u2 = d2, d2 = o3);
              var s2 = "(".concat(u2, ")").concat(a3, "(").concat(d2, ")");
              n4.startsWith("geid_") ? e2.multi && (void 0 === i3[s2] ? i3[s2] = 0 : i3[s2]++, c2 += "".concat(i3[s2], ". ")) : c2 += "[".concat(n4, "]: "), r3[c2 += s2] = t2.attributes;
            }));
            var o2 = {};
            for (var a2 in this) this.hasOwnProperty(a2) && !Ut.has(a2) && "function" != typeof this[a2] && "symbol" !== t(a2) && (o2[a2] = this[a2]);
            return o2.attributes = this._attributes, o2.nodes = n3, o2.edges = r3, p(o2, "constructor", this.constructor), o2;
          }, r2;
        })(y.exports.EventEmitter);
        "undefined" != typeof Symbol && (zt.prototype[/* @__PURE__ */ Symbol.for("nodejs.util.inspect.custom")] = zt.prototype.inspect), [{ name: function(t2) {
          return "".concat(t2, "Edge");
        }, generateKey: true }, { name: function(t2) {
          return "".concat(t2, "DirectedEdge");
        }, generateKey: true, type: "directed" }, { name: function(t2) {
          return "".concat(t2, "UndirectedEdge");
        }, generateKey: true, type: "undirected" }, { name: function(t2) {
          return "".concat(t2, "EdgeWithKey");
        } }, { name: function(t2) {
          return "".concat(t2, "DirectedEdgeWithKey");
        }, type: "directed" }, { name: function(t2) {
          return "".concat(t2, "UndirectedEdgeWithKey");
        }, type: "undirected" }].forEach((function(t2) {
          ["add", "merge", "update"].forEach((function(e2) {
            var n2 = t2.name(e2), r2 = "add" === e2 ? jt : Ct;
            t2.generateKey ? zt.prototype[n2] = function(i2, o2, a2) {
              return r2(this, n2, true, "undirected" === (t2.type || this.type), null, i2, o2, a2, "update" === e2);
            } : zt.prototype[n2] = function(i2, o2, a2, c2) {
              return r2(this, n2, false, "undirected" === (t2.type || this.type), i2, o2, a2, c2, "update" === e2);
            };
          }));
        })), (function(t2) {
          Q.forEach((function(e2) {
            var n2 = e2.name, r2 = e2.attacher;
            r2(t2, n2("Node"), 0), r2(t2, n2("Source"), 1), r2(t2, n2("Target"), 2), r2(t2, n2("Opposite"), 3);
          }));
        })(zt), (function(t2) {
          X.forEach((function(e2) {
            var n2 = e2.name, r2 = e2.attacher;
            r2(t2, n2("Edge"), "mixed"), r2(t2, n2("DirectedEdge"), "directed"), r2(t2, n2("UndirectedEdge"), "undirected");
          }));
        })(zt), (function(t2) {
          et.forEach((function(e2) {
            !(function(t3, e3) {
              var n2 = e3.name, r2 = e3.type, i2 = e3.direction;
              t3.prototype[n2] = function(t4, e4) {
                if ("mixed" !== r2 && "mixed" !== this.type && r2 !== this.type) return [];
                if (!arguments.length) return ut(this, r2);
                if (1 === arguments.length) {
                  t4 = "" + t4;
                  var o2 = this._nodes.get(t4);
                  if (void 0 === o2) throw new F("Graph.".concat(n2, ': could not find the "').concat(t4, '" node in the graph.'));
                  return pt(this.multi, "mixed" === r2 ? this.type : r2, i2, o2);
                }
                if (2 === arguments.length) {
                  t4 = "" + t4, e4 = "" + e4;
                  var a2 = this._nodes.get(t4);
                  if (!a2) throw new F("Graph.".concat(n2, ':  could not find the "').concat(t4, '" source node in the graph.'));
                  if (!this._nodes.has(e4)) throw new F("Graph.".concat(n2, ':  could not find the "').concat(e4, '" target node in the graph.'));
                  return gt(r2, this.multi, i2, a2, e4);
                }
                throw new B("Graph.".concat(n2, ": too many arguments (expecting 0, 1 or 2 and got ").concat(arguments.length, ")."));
              };
            })(t2, e2), (function(t3, e3) {
              var n2 = e3.name, r2 = e3.type, i2 = e3.direction, o2 = "forEach" + n2[0].toUpperCase() + n2.slice(1, -1);
              t3.prototype[o2] = function(t4, e4, n3) {
                if ("mixed" === r2 || "mixed" === this.type || r2 === this.type) {
                  if (1 === arguments.length) return dt(false, this, r2, n3 = t4);
                  if (2 === arguments.length) {
                    t4 = "" + t4, n3 = e4;
                    var a3 = this._nodes.get(t4);
                    if (void 0 === a3) throw new F("Graph.".concat(o2, ': could not find the "').concat(t4, '" node in the graph.'));
                    return ht(false, this.multi, "mixed" === r2 ? this.type : r2, i2, a3, n3);
                  }
                  if (3 === arguments.length) {
                    t4 = "" + t4, e4 = "" + e4;
                    var c3 = this._nodes.get(t4);
                    if (!c3) throw new F("Graph.".concat(o2, ':  could not find the "').concat(t4, '" source node in the graph.'));
                    if (!this._nodes.has(e4)) throw new F("Graph.".concat(o2, ':  could not find the "').concat(e4, '" target node in the graph.'));
                    return lt(false, r2, this.multi, i2, c3, e4, n3);
                  }
                  throw new B("Graph.".concat(o2, ": too many arguments (expecting 1, 2 or 3 and got ").concat(arguments.length, ")."));
                }
              };
              var a2 = "map" + n2[0].toUpperCase() + n2.slice(1);
              t3.prototype[a2] = function() {
                var t4, e4 = Array.prototype.slice.call(arguments), n3 = e4.pop();
                if (0 === e4.length) {
                  var i3 = 0;
                  "directed" !== r2 && (i3 += this.undirectedSize), "undirected" !== r2 && (i3 += this.directedSize), t4 = new Array(i3);
                  var a3 = 0;
                  e4.push((function(e5, r3, i4, o3, c3, u3, d2) {
                    t4[a3++] = n3(e5, r3, i4, o3, c3, u3, d2);
                  }));
                } else t4 = [], e4.push((function(e5, r3, i4, o3, a4, c3, u3) {
                  t4.push(n3(e5, r3, i4, o3, a4, c3, u3));
                }));
                return this[o2].apply(this, e4), t4;
              };
              var c2 = "filter" + n2[0].toUpperCase() + n2.slice(1);
              t3.prototype[c2] = function() {
                var t4 = Array.prototype.slice.call(arguments), e4 = t4.pop(), n3 = [];
                return t4.push((function(t5, r3, i3, o3, a3, c3, u3) {
                  e4(t5, r3, i3, o3, a3, c3, u3) && n3.push(t5);
                })), this[o2].apply(this, t4), n3;
              };
              var u2 = "reduce" + n2[0].toUpperCase() + n2.slice(1);
              t3.prototype[u2] = function() {
                var t4, e4, n3 = Array.prototype.slice.call(arguments);
                if (n3.length < 2 || n3.length > 4) throw new B("Graph.".concat(u2, ": invalid number of arguments (expecting 2, 3 or 4 and got ").concat(n3.length, ")."));
                if ("function" == typeof n3[n3.length - 1] && "function" != typeof n3[n3.length - 2]) throw new B("Graph.".concat(u2, ": missing initial value. You must provide it because the callback takes more than one argument and we cannot infer the initial value from the first iteration, as you could with a simple array."));
                2 === n3.length ? (t4 = n3[0], e4 = n3[1], n3 = []) : 3 === n3.length ? (t4 = n3[1], e4 = n3[2], n3 = [n3[0]]) : 4 === n3.length && (t4 = n3[2], e4 = n3[3], n3 = [n3[0], n3[1]]);
                var r3 = e4;
                return n3.push((function(e5, n4, i3, o3, a3, c3, u3) {
                  r3 = t4(r3, e5, n4, i3, o3, a3, c3, u3);
                })), this[o2].apply(this, n3), r3;
              };
            })(t2, e2), (function(t3, e3) {
              var n2 = e3.name, r2 = e3.type, i2 = e3.direction, o2 = "find" + n2[0].toUpperCase() + n2.slice(1, -1);
              t3.prototype[o2] = function(t4, e4, n3) {
                if ("mixed" !== r2 && "mixed" !== this.type && r2 !== this.type) return false;
                if (1 === arguments.length) return dt(true, this, r2, n3 = t4);
                if (2 === arguments.length) {
                  t4 = "" + t4, n3 = e4;
                  var a3 = this._nodes.get(t4);
                  if (void 0 === a3) throw new F("Graph.".concat(o2, ': could not find the "').concat(t4, '" node in the graph.'));
                  return ht(true, this.multi, "mixed" === r2 ? this.type : r2, i2, a3, n3);
                }
                if (3 === arguments.length) {
                  t4 = "" + t4, e4 = "" + e4;
                  var c3 = this._nodes.get(t4);
                  if (!c3) throw new F("Graph.".concat(o2, ':  could not find the "').concat(t4, '" source node in the graph.'));
                  if (!this._nodes.has(e4)) throw new F("Graph.".concat(o2, ':  could not find the "').concat(e4, '" target node in the graph.'));
                  return lt(true, r2, this.multi, i2, c3, e4, n3);
                }
                throw new B("Graph.".concat(o2, ": too many arguments (expecting 1, 2 or 3 and got ").concat(arguments.length, ")."));
              };
              var a2 = "some" + n2[0].toUpperCase() + n2.slice(1, -1);
              t3.prototype[a2] = function() {
                var t4 = Array.prototype.slice.call(arguments), e4 = t4.pop();
                return t4.push((function(t5, n3, r3, i3, o3, a3, c3) {
                  return e4(t5, n3, r3, i3, o3, a3, c3);
                })), !!this[o2].apply(this, t4);
              };
              var c2 = "every" + n2[0].toUpperCase() + n2.slice(1, -1);
              t3.prototype[c2] = function() {
                var t4 = Array.prototype.slice.call(arguments), e4 = t4.pop();
                return t4.push((function(t5, n3, r3, i3, o3, a3, c3) {
                  return !e4(t5, n3, r3, i3, o3, a3, c3);
                })), !this[o2].apply(this, t4);
              };
            })(t2, e2), (function(t3, e3) {
              var n2 = e3.name, r2 = e3.type, i2 = e3.direction, o2 = n2.slice(0, -1) + "Entries";
              t3.prototype[o2] = function(t4, e4) {
                if ("mixed" !== r2 && "mixed" !== this.type && r2 !== this.type) return O.empty();
                if (!arguments.length) return st(this, r2);
                if (1 === arguments.length) {
                  t4 = "" + t4;
                  var n3 = this._nodes.get(t4);
                  if (!n3) throw new F("Graph.".concat(o2, ': could not find the "').concat(t4, '" node in the graph.'));
                  return ft(r2, i2, n3);
                }
                if (2 === arguments.length) {
                  t4 = "" + t4, e4 = "" + e4;
                  var a2 = this._nodes.get(t4);
                  if (!a2) throw new F("Graph.".concat(o2, ':  could not find the "').concat(t4, '" source node in the graph.'));
                  if (!this._nodes.has(e4)) throw new F("Graph.".concat(o2, ':  could not find the "').concat(e4, '" target node in the graph.'));
                  return yt(r2, i2, a2, e4);
                }
                throw new B("Graph.".concat(o2, ": too many arguments (expecting 0, 1 or 2 and got ").concat(arguments.length, ")."));
              };
            })(t2, e2);
          }));
        })(zt), (function(t2) {
          wt.forEach((function(e2) {
            _t(t2, e2), (function(t3, e3) {
              var n2 = e3.name, r2 = e3.type, i2 = e3.direction, o2 = "forEach" + n2[0].toUpperCase() + n2.slice(1, -1);
              t3.prototype[o2] = function(t4, e4) {
                if ("mixed" === r2 || "mixed" === this.type || r2 === this.type) {
                  t4 = "" + t4;
                  var n3 = this._nodes.get(t4);
                  if (void 0 === n3) throw new F("Graph.".concat(o2, ': could not find the "').concat(t4, '" node in the graph.'));
                  mt(false, "mixed" === r2 ? this.type : r2, i2, n3, e4);
                }
              };
              var a2 = "map" + n2[0].toUpperCase() + n2.slice(1);
              t3.prototype[a2] = function(t4, e4) {
                var n3 = [];
                return this[o2](t4, (function(t5, r3) {
                  n3.push(e4(t5, r3));
                })), n3;
              };
              var c2 = "filter" + n2[0].toUpperCase() + n2.slice(1);
              t3.prototype[c2] = function(t4, e4) {
                var n3 = [];
                return this[o2](t4, (function(t5, r3) {
                  e4(t5, r3) && n3.push(t5);
                })), n3;
              };
              var u2 = "reduce" + n2[0].toUpperCase() + n2.slice(1);
              t3.prototype[u2] = function(t4, e4, n3) {
                if (arguments.length < 3) throw new B("Graph.".concat(u2, ": missing initial value. You must provide it because the callback takes more than one argument and we cannot infer the initial value from the first iteration, as you could with a simple array."));
                var r3 = n3;
                return this[o2](t4, (function(t5, n4) {
                  r3 = e4(r3, t5, n4);
                })), r3;
              };
            })(t2, e2), (function(t3, e3) {
              var n2 = e3.name, r2 = e3.type, i2 = e3.direction, o2 = n2[0].toUpperCase() + n2.slice(1, -1), a2 = "find" + o2;
              t3.prototype[a2] = function(t4, e4) {
                if ("mixed" === r2 || "mixed" === this.type || r2 === this.type) {
                  t4 = "" + t4;
                  var n3 = this._nodes.get(t4);
                  if (void 0 === n3) throw new F("Graph.".concat(a2, ': could not find the "').concat(t4, '" node in the graph.'));
                  return mt(true, "mixed" === r2 ? this.type : r2, i2, n3, e4);
                }
              };
              var c2 = "some" + o2;
              t3.prototype[c2] = function(t4, e4) {
                return !!this[a2](t4, e4);
              };
              var u2 = "every" + o2;
              t3.prototype[u2] = function(t4, e4) {
                return !this[a2](t4, (function(t5, n3) {
                  return !e4(t5, n3);
                }));
              };
            })(t2, e2), Gt(t2, e2);
          }));
        })(zt);
        var Wt = (function(t2) {
          function n2(e2) {
            var n3 = u({ type: "directed" }, e2);
            if ("multi" in n3 && false !== n3.multi) throw new B("DirectedGraph.from: inconsistent indication that the graph should be multi in given options!");
            if ("directed" !== n3.type) throw new B('DirectedGraph.from: inconsistent "' + n3.type + '" type in given options!');
            return t2.call(this, n3) || this;
          }
          return e(n2, t2), n2;
        })(zt), Pt = (function(t2) {
          function n2(e2) {
            var n3 = u({ type: "undirected" }, e2);
            if ("multi" in n3 && false !== n3.multi) throw new B("UndirectedGraph.from: inconsistent indication that the graph should be multi in given options!");
            if ("undirected" !== n3.type) throw new B('UndirectedGraph.from: inconsistent "' + n3.type + '" type in given options!');
            return t2.call(this, n3) || this;
          }
          return e(n2, t2), n2;
        })(zt), Rt = (function(t2) {
          function n2(e2) {
            var n3 = u({ multi: true }, e2);
            if ("multi" in n3 && true !== n3.multi) throw new B("MultiGraph.from: inconsistent indication that the graph should be simple in given options!");
            return t2.call(this, n3) || this;
          }
          return e(n2, t2), n2;
        })(zt), Kt = (function(t2) {
          function n2(e2) {
            var n3 = u({ type: "directed", multi: true }, e2);
            if ("multi" in n3 && true !== n3.multi) throw new B("MultiDirectedGraph.from: inconsistent indication that the graph should be simple in given options!");
            if ("directed" !== n3.type) throw new B('MultiDirectedGraph.from: inconsistent "' + n3.type + '" type in given options!');
            return t2.call(this, n3) || this;
          }
          return e(n2, t2), n2;
        })(zt), Tt = (function(t2) {
          function n2(e2) {
            var n3 = u({ type: "undirected", multi: true }, e2);
            if ("multi" in n3 && true !== n3.multi) throw new B("MultiUndirectedGraph.from: inconsistent indication that the graph should be simple in given options!");
            if ("undirected" !== n3.type) throw new B('MultiUndirectedGraph.from: inconsistent "' + n3.type + '" type in given options!');
            return t2.call(this, n3) || this;
          }
          return e(n2, t2), n2;
        })(zt);
        function Bt(t2) {
          t2.from = function(e2, n2) {
            var r2 = u({}, e2.options, n2), i2 = new t2(r2);
            return i2.import(e2), i2;
          };
        }
        return Bt(zt), Bt(Wt), Bt(Pt), Bt(Rt), Bt(Kt), Bt(Tt), zt.Graph = zt, zt.DirectedGraph = Wt, zt.UndirectedGraph = Pt, zt.MultiGraph = Rt, zt.MultiDirectedGraph = Kt, zt.MultiUndirectedGraph = Tt, zt.InvalidArgumentsGraphError = B, zt.NotFoundGraphError = F, zt.UsageGraphError = I, zt;
      }));
    }
  });

  // node_modules/events/events.js
  var require_events = __commonJS({
    "node_modules/events/events.js"(exports, module) {
      "use strict";
      var R = typeof Reflect === "object" ? Reflect : null;
      var ReflectApply = R && typeof R.apply === "function" ? R.apply : function ReflectApply2(target, receiver, args) {
        return Function.prototype.apply.call(target, receiver, args);
      };
      var ReflectOwnKeys;
      if (R && typeof R.ownKeys === "function") {
        ReflectOwnKeys = R.ownKeys;
      } else if (Object.getOwnPropertySymbols) {
        ReflectOwnKeys = function ReflectOwnKeys2(target) {
          return Object.getOwnPropertyNames(target).concat(Object.getOwnPropertySymbols(target));
        };
      } else {
        ReflectOwnKeys = function ReflectOwnKeys2(target) {
          return Object.getOwnPropertyNames(target);
        };
      }
      function ProcessEmitWarning(warning) {
        if (console && console.warn) console.warn(warning);
      }
      var NumberIsNaN = Number.isNaN || function NumberIsNaN2(value) {
        return value !== value;
      };
      function EventEmitter2() {
        EventEmitter2.init.call(this);
      }
      module.exports = EventEmitter2;
      module.exports.once = once;
      EventEmitter2.EventEmitter = EventEmitter2;
      EventEmitter2.prototype._events = void 0;
      EventEmitter2.prototype._eventsCount = 0;
      EventEmitter2.prototype._maxListeners = void 0;
      var defaultMaxListeners = 10;
      function checkListener(listener) {
        if (typeof listener !== "function") {
          throw new TypeError('The "listener" argument must be of type Function. Received type ' + typeof listener);
        }
      }
      Object.defineProperty(EventEmitter2, "defaultMaxListeners", {
        enumerable: true,
        get: function() {
          return defaultMaxListeners;
        },
        set: function(arg) {
          if (typeof arg !== "number" || arg < 0 || NumberIsNaN(arg)) {
            throw new RangeError('The value of "defaultMaxListeners" is out of range. It must be a non-negative number. Received ' + arg + ".");
          }
          defaultMaxListeners = arg;
        }
      });
      EventEmitter2.init = function() {
        if (this._events === void 0 || this._events === Object.getPrototypeOf(this)._events) {
          this._events = /* @__PURE__ */ Object.create(null);
          this._eventsCount = 0;
        }
        this._maxListeners = this._maxListeners || void 0;
      };
      EventEmitter2.prototype.setMaxListeners = function setMaxListeners(n) {
        if (typeof n !== "number" || n < 0 || NumberIsNaN(n)) {
          throw new RangeError('The value of "n" is out of range. It must be a non-negative number. Received ' + n + ".");
        }
        this._maxListeners = n;
        return this;
      };
      function _getMaxListeners(that) {
        if (that._maxListeners === void 0)
          return EventEmitter2.defaultMaxListeners;
        return that._maxListeners;
      }
      EventEmitter2.prototype.getMaxListeners = function getMaxListeners() {
        return _getMaxListeners(this);
      };
      EventEmitter2.prototype.emit = function emit(type) {
        var args = [];
        for (var i = 1; i < arguments.length; i++) args.push(arguments[i]);
        var doError = type === "error";
        var events = this._events;
        if (events !== void 0)
          doError = doError && events.error === void 0;
        else if (!doError)
          return false;
        if (doError) {
          var er;
          if (args.length > 0)
            er = args[0];
          if (er instanceof Error) {
            throw er;
          }
          var err = new Error("Unhandled error." + (er ? " (" + er.message + ")" : ""));
          err.context = er;
          throw err;
        }
        var handler = events[type];
        if (handler === void 0)
          return false;
        if (typeof handler === "function") {
          ReflectApply(handler, this, args);
        } else {
          var len = handler.length;
          var listeners = arrayClone(handler, len);
          for (var i = 0; i < len; ++i)
            ReflectApply(listeners[i], this, args);
        }
        return true;
      };
      function _addListener(target, type, listener, prepend) {
        var m;
        var events;
        var existing;
        checkListener(listener);
        events = target._events;
        if (events === void 0) {
          events = target._events = /* @__PURE__ */ Object.create(null);
          target._eventsCount = 0;
        } else {
          if (events.newListener !== void 0) {
            target.emit(
              "newListener",
              type,
              listener.listener ? listener.listener : listener
            );
            events = target._events;
          }
          existing = events[type];
        }
        if (existing === void 0) {
          existing = events[type] = listener;
          ++target._eventsCount;
        } else {
          if (typeof existing === "function") {
            existing = events[type] = prepend ? [listener, existing] : [existing, listener];
          } else if (prepend) {
            existing.unshift(listener);
          } else {
            existing.push(listener);
          }
          m = _getMaxListeners(target);
          if (m > 0 && existing.length > m && !existing.warned) {
            existing.warned = true;
            var w = new Error("Possible EventEmitter memory leak detected. " + existing.length + " " + String(type) + " listeners added. Use emitter.setMaxListeners() to increase limit");
            w.name = "MaxListenersExceededWarning";
            w.emitter = target;
            w.type = type;
            w.count = existing.length;
            ProcessEmitWarning(w);
          }
        }
        return target;
      }
      EventEmitter2.prototype.addListener = function addListener(type, listener) {
        return _addListener(this, type, listener, false);
      };
      EventEmitter2.prototype.on = EventEmitter2.prototype.addListener;
      EventEmitter2.prototype.prependListener = function prependListener(type, listener) {
        return _addListener(this, type, listener, true);
      };
      function onceWrapper() {
        if (!this.fired) {
          this.target.removeListener(this.type, this.wrapFn);
          this.fired = true;
          if (arguments.length === 0)
            return this.listener.call(this.target);
          return this.listener.apply(this.target, arguments);
        }
      }
      function _onceWrap(target, type, listener) {
        var state = { fired: false, wrapFn: void 0, target, type, listener };
        var wrapped = onceWrapper.bind(state);
        wrapped.listener = listener;
        state.wrapFn = wrapped;
        return wrapped;
      }
      EventEmitter2.prototype.once = function once2(type, listener) {
        checkListener(listener);
        this.on(type, _onceWrap(this, type, listener));
        return this;
      };
      EventEmitter2.prototype.prependOnceListener = function prependOnceListener(type, listener) {
        checkListener(listener);
        this.prependListener(type, _onceWrap(this, type, listener));
        return this;
      };
      EventEmitter2.prototype.removeListener = function removeListener(type, listener) {
        var list, events, position, i, originalListener;
        checkListener(listener);
        events = this._events;
        if (events === void 0)
          return this;
        list = events[type];
        if (list === void 0)
          return this;
        if (list === listener || list.listener === listener) {
          if (--this._eventsCount === 0)
            this._events = /* @__PURE__ */ Object.create(null);
          else {
            delete events[type];
            if (events.removeListener)
              this.emit("removeListener", type, list.listener || listener);
          }
        } else if (typeof list !== "function") {
          position = -1;
          for (i = list.length - 1; i >= 0; i--) {
            if (list[i] === listener || list[i].listener === listener) {
              originalListener = list[i].listener;
              position = i;
              break;
            }
          }
          if (position < 0)
            return this;
          if (position === 0)
            list.shift();
          else {
            spliceOne(list, position);
          }
          if (list.length === 1)
            events[type] = list[0];
          if (events.removeListener !== void 0)
            this.emit("removeListener", type, originalListener || listener);
        }
        return this;
      };
      EventEmitter2.prototype.off = EventEmitter2.prototype.removeListener;
      EventEmitter2.prototype.removeAllListeners = function removeAllListeners(type) {
        var listeners, events, i;
        events = this._events;
        if (events === void 0)
          return this;
        if (events.removeListener === void 0) {
          if (arguments.length === 0) {
            this._events = /* @__PURE__ */ Object.create(null);
            this._eventsCount = 0;
          } else if (events[type] !== void 0) {
            if (--this._eventsCount === 0)
              this._events = /* @__PURE__ */ Object.create(null);
            else
              delete events[type];
          }
          return this;
        }
        if (arguments.length === 0) {
          var keys = Object.keys(events);
          var key;
          for (i = 0; i < keys.length; ++i) {
            key = keys[i];
            if (key === "removeListener") continue;
            this.removeAllListeners(key);
          }
          this.removeAllListeners("removeListener");
          this._events = /* @__PURE__ */ Object.create(null);
          this._eventsCount = 0;
          return this;
        }
        listeners = events[type];
        if (typeof listeners === "function") {
          this.removeListener(type, listeners);
        } else if (listeners !== void 0) {
          for (i = listeners.length - 1; i >= 0; i--) {
            this.removeListener(type, listeners[i]);
          }
        }
        return this;
      };
      function _listeners(target, type, unwrap) {
        var events = target._events;
        if (events === void 0)
          return [];
        var evlistener = events[type];
        if (evlistener === void 0)
          return [];
        if (typeof evlistener === "function")
          return unwrap ? [evlistener.listener || evlistener] : [evlistener];
        return unwrap ? unwrapListeners(evlistener) : arrayClone(evlistener, evlistener.length);
      }
      EventEmitter2.prototype.listeners = function listeners(type) {
        return _listeners(this, type, true);
      };
      EventEmitter2.prototype.rawListeners = function rawListeners(type) {
        return _listeners(this, type, false);
      };
      EventEmitter2.listenerCount = function(emitter, type) {
        if (typeof emitter.listenerCount === "function") {
          return emitter.listenerCount(type);
        } else {
          return listenerCount.call(emitter, type);
        }
      };
      EventEmitter2.prototype.listenerCount = listenerCount;
      function listenerCount(type) {
        var events = this._events;
        if (events !== void 0) {
          var evlistener = events[type];
          if (typeof evlistener === "function") {
            return 1;
          } else if (evlistener !== void 0) {
            return evlistener.length;
          }
        }
        return 0;
      }
      EventEmitter2.prototype.eventNames = function eventNames() {
        return this._eventsCount > 0 ? ReflectOwnKeys(this._events) : [];
      };
      function arrayClone(arr, n) {
        var copy = new Array(n);
        for (var i = 0; i < n; ++i)
          copy[i] = arr[i];
        return copy;
      }
      function spliceOne(list, index) {
        for (; index + 1 < list.length; index++)
          list[index] = list[index + 1];
        list.pop();
      }
      function unwrapListeners(arr) {
        var ret = new Array(arr.length);
        for (var i = 0; i < ret.length; ++i) {
          ret[i] = arr[i].listener || arr[i];
        }
        return ret;
      }
      function once(emitter, name) {
        return new Promise(function(resolve, reject) {
          function errorListener(err) {
            emitter.removeListener(name, resolver);
            reject(err);
          }
          function resolver() {
            if (typeof emitter.removeListener === "function") {
              emitter.removeListener("error", errorListener);
            }
            resolve([].slice.call(arguments));
          }
          ;
          eventTargetAgnosticAddListener(emitter, name, resolver, { once: true });
          if (name !== "error") {
            addErrorHandlerIfEventEmitter(emitter, errorListener, { once: true });
          }
        });
      }
      function addErrorHandlerIfEventEmitter(emitter, handler, flags) {
        if (typeof emitter.on === "function") {
          eventTargetAgnosticAddListener(emitter, "error", handler, flags);
        }
      }
      function eventTargetAgnosticAddListener(emitter, name, listener, flags) {
        if (typeof emitter.on === "function") {
          if (flags.once) {
            emitter.once(name, listener);
          } else {
            emitter.on(name, listener);
          }
        } else if (typeof emitter.addEventListener === "function") {
          emitter.addEventListener(name, function wrapListener(arg) {
            if (flags.once) {
              emitter.removeEventListener(name, wrapListener);
            }
            listener(arg);
          });
        } else {
          throw new TypeError('The "emitter" argument must be of type EventEmitter. Received type ' + typeof emitter);
        }
      }
    }
  });

  // node_modules/graphology-utils/is-graph.js
  var require_is_graph = __commonJS({
    "node_modules/graphology-utils/is-graph.js"(exports, module) {
      module.exports = function isGraph2(value) {
        return value !== null && typeof value === "object" && typeof value.addUndirectedEdgeWithKey === "function" && typeof value.dropNode === "function" && typeof value.multi === "boolean";
      };
    }
  });

  // node_modules/graphology-utils/getters.js
  var require_getters = __commonJS({
    "node_modules/graphology-utils/getters.js"(exports) {
      function coerceWeight(value) {
        if (typeof value !== "number" || isNaN(value)) return 1;
        return value;
      }
      function createNodeValueGetter(nameOrFunction, defaultValue) {
        var getter = {};
        var coerceToDefault = function(v) {
          if (typeof v === "undefined") return defaultValue;
          return v;
        };
        if (typeof defaultValue === "function") coerceToDefault = defaultValue;
        var get = function(attributes) {
          return coerceToDefault(attributes[nameOrFunction]);
        };
        var returnDefault = function() {
          return coerceToDefault(void 0);
        };
        if (typeof nameOrFunction === "string") {
          getter.fromAttributes = get;
          getter.fromGraph = function(graph, node) {
            return get(graph.getNodeAttributes(node));
          };
          getter.fromEntry = function(node, attributes) {
            return get(attributes);
          };
        } else if (typeof nameOrFunction === "function") {
          getter.fromAttributes = function() {
            throw new Error(
              "graphology-utils/getters/createNodeValueGetter: irrelevant usage."
            );
          };
          getter.fromGraph = function(graph, node) {
            return coerceToDefault(
              nameOrFunction(node, graph.getNodeAttributes(node))
            );
          };
          getter.fromEntry = function(node, attributes) {
            return coerceToDefault(nameOrFunction(node, attributes));
          };
        } else {
          getter.fromAttributes = returnDefault;
          getter.fromGraph = returnDefault;
          getter.fromEntry = returnDefault;
        }
        return getter;
      }
      function createEdgeValueGetter(nameOrFunction, defaultValue) {
        var getter = {};
        var coerceToDefault = function(v) {
          if (typeof v === "undefined") return defaultValue;
          return v;
        };
        if (typeof defaultValue === "function") coerceToDefault = defaultValue;
        var get = function(attributes) {
          return coerceToDefault(attributes[nameOrFunction]);
        };
        var returnDefault = function() {
          return coerceToDefault(void 0);
        };
        if (typeof nameOrFunction === "string") {
          getter.fromAttributes = get;
          getter.fromGraph = function(graph, edge) {
            return get(graph.getEdgeAttributes(edge));
          };
          getter.fromEntry = function(edge, attributes) {
            return get(attributes);
          };
          getter.fromPartialEntry = getter.fromEntry;
          getter.fromMinimalEntry = getter.fromEntry;
        } else if (typeof nameOrFunction === "function") {
          getter.fromAttributes = function() {
            throw new Error(
              "graphology-utils/getters/createEdgeValueGetter: irrelevant usage."
            );
          };
          getter.fromGraph = function(graph, edge) {
            var extremities = graph.extremities(edge);
            return coerceToDefault(
              nameOrFunction(
                edge,
                graph.getEdgeAttributes(edge),
                extremities[0],
                extremities[1],
                graph.getNodeAttributes(extremities[0]),
                graph.getNodeAttributes(extremities[1]),
                graph.isUndirected(edge)
              )
            );
          };
          getter.fromEntry = function(e, a, s, t, sa, ta, u) {
            return coerceToDefault(nameOrFunction(e, a, s, t, sa, ta, u));
          };
          getter.fromPartialEntry = function(e, a, s, t) {
            return coerceToDefault(nameOrFunction(e, a, s, t));
          };
          getter.fromMinimalEntry = function(e, a) {
            return coerceToDefault(nameOrFunction(e, a));
          };
        } else {
          getter.fromAttributes = returnDefault;
          getter.fromGraph = returnDefault;
          getter.fromEntry = returnDefault;
          getter.fromMinimalEntry = returnDefault;
        }
        return getter;
      }
      exports.createNodeValueGetter = createNodeValueGetter;
      exports.createEdgeValueGetter = createEdgeValueGetter;
      exports.createEdgeWeightGetter = function(name) {
        return createEdgeValueGetter(name, coerceWeight);
      };
    }
  });

  // node_modules/graphology-layout-forceatlas2/iterate.js
  var require_iterate = __commonJS({
    "node_modules/graphology-layout-forceatlas2/iterate.js"(exports, module) {
      var NODE_X = 0;
      var NODE_Y = 1;
      var NODE_DX = 2;
      var NODE_DY = 3;
      var NODE_OLD_DX = 4;
      var NODE_OLD_DY = 5;
      var NODE_MASS = 6;
      var NODE_CONVERGENCE = 7;
      var NODE_SIZE = 8;
      var NODE_FIXED = 9;
      var EDGE_SOURCE = 0;
      var EDGE_TARGET = 1;
      var EDGE_WEIGHT = 2;
      var REGION_NODE = 0;
      var REGION_CENTER_X = 1;
      var REGION_CENTER_Y = 2;
      var REGION_SIZE = 3;
      var REGION_NEXT_SIBLING = 4;
      var REGION_FIRST_CHILD = 5;
      var REGION_MASS = 6;
      var REGION_MASS_CENTER_X = 7;
      var REGION_MASS_CENTER_Y = 8;
      var SUBDIVISION_ATTEMPTS = 3;
      var PPN = 10;
      var PPE = 3;
      var PPR = 9;
      var MAX_FORCE = 10;
      module.exports = function iterate(options, NodeMatrix, EdgeMatrix) {
        var l, r, n, n1, n2, rn, e, w, g, s;
        var order = NodeMatrix.length, size = EdgeMatrix.length;
        var adjustSizes = options.adjustSizes;
        var thetaSquared = options.barnesHutTheta * options.barnesHutTheta;
        var outboundAttCompensation, coefficient, xDist, yDist, ewc, distance, factor;
        var RegionMatrix = [];
        for (n = 0; n < order; n += PPN) {
          NodeMatrix[n + NODE_OLD_DX] = NodeMatrix[n + NODE_DX];
          NodeMatrix[n + NODE_OLD_DY] = NodeMatrix[n + NODE_DY];
          NodeMatrix[n + NODE_DX] = 0;
          NodeMatrix[n + NODE_DY] = 0;
        }
        if (options.outboundAttractionDistribution) {
          outboundAttCompensation = 0;
          for (n = 0; n < order; n += PPN) {
            outboundAttCompensation += NodeMatrix[n + NODE_MASS];
          }
          outboundAttCompensation /= order / PPN;
        }
        if (options.barnesHutOptimize) {
          var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, q, q2, subdivisionAttempts;
          for (n = 0; n < order; n += PPN) {
            minX = Math.min(minX, NodeMatrix[n + NODE_X]);
            maxX = Math.max(maxX, NodeMatrix[n + NODE_X]);
            minY = Math.min(minY, NodeMatrix[n + NODE_Y]);
            maxY = Math.max(maxY, NodeMatrix[n + NODE_Y]);
          }
          var dx = maxX - minX, dy = maxY - minY;
          if (dx > dy) {
            minY -= (dx - dy) / 2;
            maxY = minY + dx;
          } else {
            minX -= (dy - dx) / 2;
            maxX = minX + dy;
          }
          RegionMatrix[0 + REGION_NODE] = -1;
          RegionMatrix[0 + REGION_CENTER_X] = (minX + maxX) / 2;
          RegionMatrix[0 + REGION_CENTER_Y] = (minY + maxY) / 2;
          RegionMatrix[0 + REGION_SIZE] = Math.max(maxX - minX, maxY - minY);
          RegionMatrix[0 + REGION_NEXT_SIBLING] = -1;
          RegionMatrix[0 + REGION_FIRST_CHILD] = -1;
          RegionMatrix[0 + REGION_MASS] = 0;
          RegionMatrix[0 + REGION_MASS_CENTER_X] = 0;
          RegionMatrix[0 + REGION_MASS_CENTER_Y] = 0;
          l = 1;
          for (n = 0; n < order; n += PPN) {
            r = 0;
            subdivisionAttempts = SUBDIVISION_ATTEMPTS;
            while (true) {
              if (RegionMatrix[r + REGION_FIRST_CHILD] >= 0) {
                if (NodeMatrix[n + NODE_X] < RegionMatrix[r + REGION_CENTER_X]) {
                  if (NodeMatrix[n + NODE_Y] < RegionMatrix[r + REGION_CENTER_Y]) {
                    q = RegionMatrix[r + REGION_FIRST_CHILD];
                  } else {
                    q = RegionMatrix[r + REGION_FIRST_CHILD] + PPR;
                  }
                } else {
                  if (NodeMatrix[n + NODE_Y] < RegionMatrix[r + REGION_CENTER_Y]) {
                    q = RegionMatrix[r + REGION_FIRST_CHILD] + PPR * 2;
                  } else {
                    q = RegionMatrix[r + REGION_FIRST_CHILD] + PPR * 3;
                  }
                }
                RegionMatrix[r + REGION_MASS_CENTER_X] = (RegionMatrix[r + REGION_MASS_CENTER_X] * RegionMatrix[r + REGION_MASS] + NodeMatrix[n + NODE_X] * NodeMatrix[n + NODE_MASS]) / (RegionMatrix[r + REGION_MASS] + NodeMatrix[n + NODE_MASS]);
                RegionMatrix[r + REGION_MASS_CENTER_Y] = (RegionMatrix[r + REGION_MASS_CENTER_Y] * RegionMatrix[r + REGION_MASS] + NodeMatrix[n + NODE_Y] * NodeMatrix[n + NODE_MASS]) / (RegionMatrix[r + REGION_MASS] + NodeMatrix[n + NODE_MASS]);
                RegionMatrix[r + REGION_MASS] += NodeMatrix[n + NODE_MASS];
                r = q;
                continue;
              } else {
                if (RegionMatrix[r + REGION_NODE] < 0) {
                  RegionMatrix[r + REGION_NODE] = n;
                  break;
                } else {
                  RegionMatrix[r + REGION_FIRST_CHILD] = l * PPR;
                  w = RegionMatrix[r + REGION_SIZE] / 2;
                  g = RegionMatrix[r + REGION_FIRST_CHILD];
                  RegionMatrix[g + REGION_NODE] = -1;
                  RegionMatrix[g + REGION_CENTER_X] = RegionMatrix[r + REGION_CENTER_X] - w;
                  RegionMatrix[g + REGION_CENTER_Y] = RegionMatrix[r + REGION_CENTER_Y] - w;
                  RegionMatrix[g + REGION_SIZE] = w;
                  RegionMatrix[g + REGION_NEXT_SIBLING] = g + PPR;
                  RegionMatrix[g + REGION_FIRST_CHILD] = -1;
                  RegionMatrix[g + REGION_MASS] = 0;
                  RegionMatrix[g + REGION_MASS_CENTER_X] = 0;
                  RegionMatrix[g + REGION_MASS_CENTER_Y] = 0;
                  g += PPR;
                  RegionMatrix[g + REGION_NODE] = -1;
                  RegionMatrix[g + REGION_CENTER_X] = RegionMatrix[r + REGION_CENTER_X] - w;
                  RegionMatrix[g + REGION_CENTER_Y] = RegionMatrix[r + REGION_CENTER_Y] + w;
                  RegionMatrix[g + REGION_SIZE] = w;
                  RegionMatrix[g + REGION_NEXT_SIBLING] = g + PPR;
                  RegionMatrix[g + REGION_FIRST_CHILD] = -1;
                  RegionMatrix[g + REGION_MASS] = 0;
                  RegionMatrix[g + REGION_MASS_CENTER_X] = 0;
                  RegionMatrix[g + REGION_MASS_CENTER_Y] = 0;
                  g += PPR;
                  RegionMatrix[g + REGION_NODE] = -1;
                  RegionMatrix[g + REGION_CENTER_X] = RegionMatrix[r + REGION_CENTER_X] + w;
                  RegionMatrix[g + REGION_CENTER_Y] = RegionMatrix[r + REGION_CENTER_Y] - w;
                  RegionMatrix[g + REGION_SIZE] = w;
                  RegionMatrix[g + REGION_NEXT_SIBLING] = g + PPR;
                  RegionMatrix[g + REGION_FIRST_CHILD] = -1;
                  RegionMatrix[g + REGION_MASS] = 0;
                  RegionMatrix[g + REGION_MASS_CENTER_X] = 0;
                  RegionMatrix[g + REGION_MASS_CENTER_Y] = 0;
                  g += PPR;
                  RegionMatrix[g + REGION_NODE] = -1;
                  RegionMatrix[g + REGION_CENTER_X] = RegionMatrix[r + REGION_CENTER_X] + w;
                  RegionMatrix[g + REGION_CENTER_Y] = RegionMatrix[r + REGION_CENTER_Y] + w;
                  RegionMatrix[g + REGION_SIZE] = w;
                  RegionMatrix[g + REGION_NEXT_SIBLING] = RegionMatrix[r + REGION_NEXT_SIBLING];
                  RegionMatrix[g + REGION_FIRST_CHILD] = -1;
                  RegionMatrix[g + REGION_MASS] = 0;
                  RegionMatrix[g + REGION_MASS_CENTER_X] = 0;
                  RegionMatrix[g + REGION_MASS_CENTER_Y] = 0;
                  l += 4;
                  if (NodeMatrix[RegionMatrix[r + REGION_NODE] + NODE_X] < RegionMatrix[r + REGION_CENTER_X]) {
                    if (NodeMatrix[RegionMatrix[r + REGION_NODE] + NODE_Y] < RegionMatrix[r + REGION_CENTER_Y]) {
                      q = RegionMatrix[r + REGION_FIRST_CHILD];
                    } else {
                      q = RegionMatrix[r + REGION_FIRST_CHILD] + PPR;
                    }
                  } else {
                    if (NodeMatrix[RegionMatrix[r + REGION_NODE] + NODE_Y] < RegionMatrix[r + REGION_CENTER_Y]) {
                      q = RegionMatrix[r + REGION_FIRST_CHILD] + PPR * 2;
                    } else {
                      q = RegionMatrix[r + REGION_FIRST_CHILD] + PPR * 3;
                    }
                  }
                  RegionMatrix[r + REGION_MASS] = NodeMatrix[RegionMatrix[r + REGION_NODE] + NODE_MASS];
                  RegionMatrix[r + REGION_MASS_CENTER_X] = NodeMatrix[RegionMatrix[r + REGION_NODE] + NODE_X];
                  RegionMatrix[r + REGION_MASS_CENTER_Y] = NodeMatrix[RegionMatrix[r + REGION_NODE] + NODE_Y];
                  RegionMatrix[q + REGION_NODE] = RegionMatrix[r + REGION_NODE];
                  RegionMatrix[r + REGION_NODE] = -1;
                  if (NodeMatrix[n + NODE_X] < RegionMatrix[r + REGION_CENTER_X]) {
                    if (NodeMatrix[n + NODE_Y] < RegionMatrix[r + REGION_CENTER_Y]) {
                      q2 = RegionMatrix[r + REGION_FIRST_CHILD];
                    } else {
                      q2 = RegionMatrix[r + REGION_FIRST_CHILD] + PPR;
                    }
                  } else {
                    if (NodeMatrix[n + NODE_Y] < RegionMatrix[r + REGION_CENTER_Y]) {
                      q2 = RegionMatrix[r + REGION_FIRST_CHILD] + PPR * 2;
                    } else {
                      q2 = RegionMatrix[r + REGION_FIRST_CHILD] + PPR * 3;
                    }
                  }
                  if (q === q2) {
                    if (subdivisionAttempts--) {
                      r = q;
                      continue;
                    } else {
                      subdivisionAttempts = SUBDIVISION_ATTEMPTS;
                      break;
                    }
                  }
                  RegionMatrix[q2 + REGION_NODE] = n;
                  break;
                }
              }
            }
          }
        }
        if (options.barnesHutOptimize) {
          coefficient = options.scalingRatio;
          for (n = 0; n < order; n += PPN) {
            r = 0;
            while (true) {
              if (RegionMatrix[r + REGION_FIRST_CHILD] >= 0) {
                distance = Math.pow(
                  NodeMatrix[n + NODE_X] - RegionMatrix[r + REGION_MASS_CENTER_X],
                  2
                ) + Math.pow(
                  NodeMatrix[n + NODE_Y] - RegionMatrix[r + REGION_MASS_CENTER_Y],
                  2
                );
                s = RegionMatrix[r + REGION_SIZE];
                if (4 * s * s / distance < thetaSquared) {
                  xDist = NodeMatrix[n + NODE_X] - RegionMatrix[r + REGION_MASS_CENTER_X];
                  yDist = NodeMatrix[n + NODE_Y] - RegionMatrix[r + REGION_MASS_CENTER_Y];
                  if (adjustSizes === true) {
                    if (distance > 0) {
                      factor = coefficient * NodeMatrix[n + NODE_MASS] * RegionMatrix[r + REGION_MASS] / distance;
                      NodeMatrix[n + NODE_DX] += xDist * factor;
                      NodeMatrix[n + NODE_DY] += yDist * factor;
                    } else if (distance < 0) {
                      factor = -coefficient * NodeMatrix[n + NODE_MASS] * RegionMatrix[r + REGION_MASS] / Math.sqrt(distance);
                      NodeMatrix[n + NODE_DX] += xDist * factor;
                      NodeMatrix[n + NODE_DY] += yDist * factor;
                    }
                  } else {
                    if (distance > 0) {
                      factor = coefficient * NodeMatrix[n + NODE_MASS] * RegionMatrix[r + REGION_MASS] / distance;
                      NodeMatrix[n + NODE_DX] += xDist * factor;
                      NodeMatrix[n + NODE_DY] += yDist * factor;
                    }
                  }
                  r = RegionMatrix[r + REGION_NEXT_SIBLING];
                  if (r < 0) break;
                  continue;
                } else {
                  r = RegionMatrix[r + REGION_FIRST_CHILD];
                  continue;
                }
              } else {
                rn = RegionMatrix[r + REGION_NODE];
                if (rn >= 0 && rn !== n) {
                  xDist = NodeMatrix[n + NODE_X] - NodeMatrix[rn + NODE_X];
                  yDist = NodeMatrix[n + NODE_Y] - NodeMatrix[rn + NODE_Y];
                  distance = xDist * xDist + yDist * yDist;
                  if (adjustSizes === true) {
                    if (distance > 0) {
                      factor = coefficient * NodeMatrix[n + NODE_MASS] * NodeMatrix[rn + NODE_MASS] / distance;
                      NodeMatrix[n + NODE_DX] += xDist * factor;
                      NodeMatrix[n + NODE_DY] += yDist * factor;
                    } else if (distance < 0) {
                      factor = -coefficient * NodeMatrix[n + NODE_MASS] * NodeMatrix[rn + NODE_MASS] / Math.sqrt(distance);
                      NodeMatrix[n + NODE_DX] += xDist * factor;
                      NodeMatrix[n + NODE_DY] += yDist * factor;
                    }
                  } else {
                    if (distance > 0) {
                      factor = coefficient * NodeMatrix[n + NODE_MASS] * NodeMatrix[rn + NODE_MASS] / distance;
                      NodeMatrix[n + NODE_DX] += xDist * factor;
                      NodeMatrix[n + NODE_DY] += yDist * factor;
                    }
                  }
                }
                r = RegionMatrix[r + REGION_NEXT_SIBLING];
                if (r < 0) break;
                continue;
              }
            }
          }
        } else {
          coefficient = options.scalingRatio;
          for (n1 = 0; n1 < order; n1 += PPN) {
            for (n2 = 0; n2 < n1; n2 += PPN) {
              xDist = NodeMatrix[n1 + NODE_X] - NodeMatrix[n2 + NODE_X];
              yDist = NodeMatrix[n1 + NODE_Y] - NodeMatrix[n2 + NODE_Y];
              if (adjustSizes === true) {
                distance = Math.sqrt(xDist * xDist + yDist * yDist) - NodeMatrix[n1 + NODE_SIZE] - NodeMatrix[n2 + NODE_SIZE];
                if (distance > 0) {
                  factor = coefficient * NodeMatrix[n1 + NODE_MASS] * NodeMatrix[n2 + NODE_MASS] / distance / distance;
                  NodeMatrix[n1 + NODE_DX] += xDist * factor;
                  NodeMatrix[n1 + NODE_DY] += yDist * factor;
                  NodeMatrix[n2 + NODE_DX] -= xDist * factor;
                  NodeMatrix[n2 + NODE_DY] -= yDist * factor;
                } else if (distance < 0) {
                  factor = 100 * coefficient * NodeMatrix[n1 + NODE_MASS] * NodeMatrix[n2 + NODE_MASS];
                  NodeMatrix[n1 + NODE_DX] += xDist * factor;
                  NodeMatrix[n1 + NODE_DY] += yDist * factor;
                  NodeMatrix[n2 + NODE_DX] -= xDist * factor;
                  NodeMatrix[n2 + NODE_DY] -= yDist * factor;
                }
              } else {
                distance = Math.sqrt(xDist * xDist + yDist * yDist);
                if (distance > 0) {
                  factor = coefficient * NodeMatrix[n1 + NODE_MASS] * NodeMatrix[n2 + NODE_MASS] / distance / distance;
                  NodeMatrix[n1 + NODE_DX] += xDist * factor;
                  NodeMatrix[n1 + NODE_DY] += yDist * factor;
                  NodeMatrix[n2 + NODE_DX] -= xDist * factor;
                  NodeMatrix[n2 + NODE_DY] -= yDist * factor;
                }
              }
            }
          }
        }
        g = options.gravity / options.scalingRatio;
        coefficient = options.scalingRatio;
        for (n = 0; n < order; n += PPN) {
          factor = 0;
          xDist = NodeMatrix[n + NODE_X];
          yDist = NodeMatrix[n + NODE_Y];
          distance = Math.sqrt(Math.pow(xDist, 2) + Math.pow(yDist, 2));
          if (options.strongGravityMode) {
            if (distance > 0) factor = coefficient * NodeMatrix[n + NODE_MASS] * g;
          } else {
            if (distance > 0)
              factor = coefficient * NodeMatrix[n + NODE_MASS] * g / distance;
          }
          NodeMatrix[n + NODE_DX] -= xDist * factor;
          NodeMatrix[n + NODE_DY] -= yDist * factor;
        }
        coefficient = 1 * (options.outboundAttractionDistribution ? outboundAttCompensation : 1);
        for (e = 0; e < size; e += PPE) {
          n1 = EdgeMatrix[e + EDGE_SOURCE];
          n2 = EdgeMatrix[e + EDGE_TARGET];
          w = EdgeMatrix[e + EDGE_WEIGHT];
          ewc = Math.pow(w, options.edgeWeightInfluence);
          xDist = NodeMatrix[n1 + NODE_X] - NodeMatrix[n2 + NODE_X];
          yDist = NodeMatrix[n1 + NODE_Y] - NodeMatrix[n2 + NODE_Y];
          if (adjustSizes === true) {
            distance = Math.sqrt(xDist * xDist + yDist * yDist) - NodeMatrix[n1 + NODE_SIZE] - NodeMatrix[n2 + NODE_SIZE];
            if (options.linLogMode) {
              if (options.outboundAttractionDistribution) {
                if (distance > 0) {
                  factor = -coefficient * ewc * Math.log(1 + distance) / distance / NodeMatrix[n1 + NODE_MASS];
                }
              } else {
                if (distance > 0) {
                  factor = -coefficient * ewc * Math.log(1 + distance) / distance;
                }
              }
            } else {
              if (options.outboundAttractionDistribution) {
                if (distance > 0) {
                  factor = -coefficient * ewc / NodeMatrix[n1 + NODE_MASS];
                }
              } else {
                if (distance > 0) {
                  factor = -coefficient * ewc;
                }
              }
            }
          } else {
            distance = Math.sqrt(Math.pow(xDist, 2) + Math.pow(yDist, 2));
            if (options.linLogMode) {
              if (options.outboundAttractionDistribution) {
                if (distance > 0) {
                  factor = -coefficient * ewc * Math.log(1 + distance) / distance / NodeMatrix[n1 + NODE_MASS];
                }
              } else {
                if (distance > 0)
                  factor = -coefficient * ewc * Math.log(1 + distance) / distance;
              }
            } else {
              if (options.outboundAttractionDistribution) {
                distance = 1;
                factor = -coefficient * ewc / NodeMatrix[n1 + NODE_MASS];
              } else {
                distance = 1;
                factor = -coefficient * ewc;
              }
            }
          }
          if (distance > 0) {
            NodeMatrix[n1 + NODE_DX] += xDist * factor;
            NodeMatrix[n1 + NODE_DY] += yDist * factor;
            NodeMatrix[n2 + NODE_DX] -= xDist * factor;
            NodeMatrix[n2 + NODE_DY] -= yDist * factor;
          }
        }
        var force, swinging, traction, nodespeed, newX, newY;
        if (adjustSizes === true) {
          for (n = 0; n < order; n += PPN) {
            if (NodeMatrix[n + NODE_FIXED] !== 1) {
              force = Math.sqrt(
                Math.pow(NodeMatrix[n + NODE_DX], 2) + Math.pow(NodeMatrix[n + NODE_DY], 2)
              );
              if (force > MAX_FORCE) {
                NodeMatrix[n + NODE_DX] = NodeMatrix[n + NODE_DX] * MAX_FORCE / force;
                NodeMatrix[n + NODE_DY] = NodeMatrix[n + NODE_DY] * MAX_FORCE / force;
              }
              swinging = NodeMatrix[n + NODE_MASS] * Math.sqrt(
                (NodeMatrix[n + NODE_OLD_DX] - NodeMatrix[n + NODE_DX]) * (NodeMatrix[n + NODE_OLD_DX] - NodeMatrix[n + NODE_DX]) + (NodeMatrix[n + NODE_OLD_DY] - NodeMatrix[n + NODE_DY]) * (NodeMatrix[n + NODE_OLD_DY] - NodeMatrix[n + NODE_DY])
              );
              traction = Math.sqrt(
                (NodeMatrix[n + NODE_OLD_DX] + NodeMatrix[n + NODE_DX]) * (NodeMatrix[n + NODE_OLD_DX] + NodeMatrix[n + NODE_DX]) + (NodeMatrix[n + NODE_OLD_DY] + NodeMatrix[n + NODE_DY]) * (NodeMatrix[n + NODE_OLD_DY] + NodeMatrix[n + NODE_DY])
              ) / 2;
              nodespeed = 0.1 * Math.log(1 + traction) / (1 + Math.sqrt(swinging));
              newX = NodeMatrix[n + NODE_X] + NodeMatrix[n + NODE_DX] * (nodespeed / options.slowDown);
              NodeMatrix[n + NODE_X] = newX;
              newY = NodeMatrix[n + NODE_Y] + NodeMatrix[n + NODE_DY] * (nodespeed / options.slowDown);
              NodeMatrix[n + NODE_Y] = newY;
            }
          }
        } else {
          for (n = 0; n < order; n += PPN) {
            if (NodeMatrix[n + NODE_FIXED] !== 1) {
              swinging = NodeMatrix[n + NODE_MASS] * Math.sqrt(
                (NodeMatrix[n + NODE_OLD_DX] - NodeMatrix[n + NODE_DX]) * (NodeMatrix[n + NODE_OLD_DX] - NodeMatrix[n + NODE_DX]) + (NodeMatrix[n + NODE_OLD_DY] - NodeMatrix[n + NODE_DY]) * (NodeMatrix[n + NODE_OLD_DY] - NodeMatrix[n + NODE_DY])
              );
              traction = Math.sqrt(
                (NodeMatrix[n + NODE_OLD_DX] + NodeMatrix[n + NODE_DX]) * (NodeMatrix[n + NODE_OLD_DX] + NodeMatrix[n + NODE_DX]) + (NodeMatrix[n + NODE_OLD_DY] + NodeMatrix[n + NODE_DY]) * (NodeMatrix[n + NODE_OLD_DY] + NodeMatrix[n + NODE_DY])
              ) / 2;
              nodespeed = NodeMatrix[n + NODE_CONVERGENCE] * Math.log(1 + traction) / (1 + Math.sqrt(swinging));
              NodeMatrix[n + NODE_CONVERGENCE] = Math.min(
                1,
                Math.sqrt(
                  nodespeed * (Math.pow(NodeMatrix[n + NODE_DX], 2) + Math.pow(NodeMatrix[n + NODE_DY], 2)) / (1 + Math.sqrt(swinging))
                )
              );
              newX = NodeMatrix[n + NODE_X] + NodeMatrix[n + NODE_DX] * (nodespeed / options.slowDown);
              NodeMatrix[n + NODE_X] = newX;
              newY = NodeMatrix[n + NODE_Y] + NodeMatrix[n + NODE_DY] * (nodespeed / options.slowDown);
              NodeMatrix[n + NODE_Y] = newY;
            }
          }
        }
        return {};
      };
    }
  });

  // node_modules/graphology-layout-forceatlas2/helpers.js
  var require_helpers = __commonJS({
    "node_modules/graphology-layout-forceatlas2/helpers.js"(exports) {
      var PPN = 10;
      var PPE = 3;
      exports.assign = function(target) {
        target = target || {};
        var objects = Array.prototype.slice.call(arguments).slice(1), i, k, l;
        for (i = 0, l = objects.length; i < l; i++) {
          if (!objects[i]) continue;
          for (k in objects[i]) target[k] = objects[i][k];
        }
        return target;
      };
      exports.validateSettings = function(settings) {
        if ("linLogMode" in settings && typeof settings.linLogMode !== "boolean")
          return { message: "the `linLogMode` setting should be a boolean." };
        if ("outboundAttractionDistribution" in settings && typeof settings.outboundAttractionDistribution !== "boolean")
          return {
            message: "the `outboundAttractionDistribution` setting should be a boolean."
          };
        if ("adjustSizes" in settings && typeof settings.adjustSizes !== "boolean")
          return { message: "the `adjustSizes` setting should be a boolean." };
        if ("edgeWeightInfluence" in settings && typeof settings.edgeWeightInfluence !== "number")
          return {
            message: "the `edgeWeightInfluence` setting should be a number."
          };
        if ("scalingRatio" in settings && !(typeof settings.scalingRatio === "number" && settings.scalingRatio >= 0))
          return { message: "the `scalingRatio` setting should be a number >= 0." };
        if ("strongGravityMode" in settings && typeof settings.strongGravityMode !== "boolean")
          return { message: "the `strongGravityMode` setting should be a boolean." };
        if ("gravity" in settings && !(typeof settings.gravity === "number" && settings.gravity >= 0))
          return { message: "the `gravity` setting should be a number >= 0." };
        if ("slowDown" in settings && !(typeof settings.slowDown === "number" || settings.slowDown >= 0))
          return { message: "the `slowDown` setting should be a number >= 0." };
        if ("barnesHutOptimize" in settings && typeof settings.barnesHutOptimize !== "boolean")
          return { message: "the `barnesHutOptimize` setting should be a boolean." };
        if ("barnesHutTheta" in settings && !(typeof settings.barnesHutTheta === "number" && settings.barnesHutTheta >= 0))
          return { message: "the `barnesHutTheta` setting should be a number >= 0." };
        return null;
      };
      exports.graphToByteArrays = function(graph, getEdgeWeight) {
        var order = graph.order;
        var size = graph.size;
        var index = {};
        var j;
        var NodeMatrix = new Float32Array(order * PPN);
        var EdgeMatrix = new Float32Array(size * PPE);
        j = 0;
        graph.forEachNode(function(node, attr) {
          index[node] = j;
          NodeMatrix[j] = attr.x;
          NodeMatrix[j + 1] = attr.y;
          NodeMatrix[j + 2] = 0;
          NodeMatrix[j + 3] = 0;
          NodeMatrix[j + 4] = 0;
          NodeMatrix[j + 5] = 0;
          NodeMatrix[j + 6] = 1;
          NodeMatrix[j + 7] = 1;
          NodeMatrix[j + 8] = attr.size || 1;
          NodeMatrix[j + 9] = attr.fixed ? 1 : 0;
          j += PPN;
        });
        j = 0;
        graph.forEachEdge(function(edge, attr, source, target, sa, ta, u) {
          var sj = index[source];
          var tj = index[target];
          var weight = getEdgeWeight(edge, attr, source, target, sa, ta, u);
          NodeMatrix[sj + 6] += weight;
          NodeMatrix[tj + 6] += weight;
          EdgeMatrix[j] = sj;
          EdgeMatrix[j + 1] = tj;
          EdgeMatrix[j + 2] = weight;
          j += PPE;
        });
        return {
          nodes: NodeMatrix,
          edges: EdgeMatrix
        };
      };
      exports.assignLayoutChanges = function(graph, NodeMatrix, outputReducer) {
        var i = 0;
        graph.updateEachNodeAttributes(function(node, attr) {
          attr.x = NodeMatrix[i];
          attr.y = NodeMatrix[i + 1];
          i += PPN;
          return outputReducer ? outputReducer(node, attr) : attr;
        });
      };
      exports.readGraphPositions = function(graph, NodeMatrix) {
        var i = 0;
        graph.forEachNode(function(node, attr) {
          NodeMatrix[i] = attr.x;
          NodeMatrix[i + 1] = attr.y;
          i += PPN;
        });
      };
      exports.collectLayoutChanges = function(graph, NodeMatrix, outputReducer) {
        var nodes = graph.nodes(), positions = {};
        for (var i = 0, j = 0, l = NodeMatrix.length; i < l; i += PPN) {
          if (outputReducer) {
            var newAttr = Object.assign({}, graph.getNodeAttributes(nodes[j]));
            newAttr.x = NodeMatrix[i];
            newAttr.y = NodeMatrix[i + 1];
            newAttr = outputReducer(nodes[j], newAttr);
            positions[nodes[j]] = {
              x: newAttr.x,
              y: newAttr.y
            };
          } else {
            positions[nodes[j]] = {
              x: NodeMatrix[i],
              y: NodeMatrix[i + 1]
            };
          }
          j++;
        }
        return positions;
      };
      exports.createWorker = function createWorker(fn) {
        var xURL = window.URL || window.webkitURL;
        var code = fn.toString();
        var objectUrl = xURL.createObjectURL(
          new Blob(["(" + code + ").call(this);"], { type: "text/javascript" })
        );
        var worker = new Worker(objectUrl);
        xURL.revokeObjectURL(objectUrl);
        return worker;
      };
    }
  });

  // node_modules/graphology-layout-forceatlas2/defaults.js
  var require_defaults = __commonJS({
    "node_modules/graphology-layout-forceatlas2/defaults.js"(exports, module) {
      module.exports = {
        linLogMode: false,
        outboundAttractionDistribution: false,
        adjustSizes: false,
        edgeWeightInfluence: 1,
        scalingRatio: 1,
        strongGravityMode: false,
        gravity: 1,
        slowDown: 1,
        barnesHutOptimize: false,
        barnesHutTheta: 0.5
      };
    }
  });

  // node_modules/graphology-layout-forceatlas2/index.js
  var require_graphology_layout_forceatlas2 = __commonJS({
    "node_modules/graphology-layout-forceatlas2/index.js"(exports, module) {
      var isGraph2 = require_is_graph();
      var createEdgeWeightGetter = require_getters().createEdgeWeightGetter;
      var iterate = require_iterate();
      var helpers = require_helpers();
      var DEFAULT_SETTINGS2 = require_defaults();
      function abstractSynchronousLayout(assign2, graph, params) {
        if (!isGraph2(graph))
          throw new Error(
            "graphology-layout-forceatlas2: the given graph is not a valid graphology instance."
          );
        if (typeof params === "number") params = { iterations: params };
        var iterations = params.iterations;
        if (typeof iterations !== "number")
          throw new Error(
            "graphology-layout-forceatlas2: invalid number of iterations."
          );
        if (iterations <= 0)
          throw new Error(
            "graphology-layout-forceatlas2: you should provide a positive number of iterations."
          );
        var getEdgeWeight = createEdgeWeightGetter(
          "getEdgeWeight" in params ? params.getEdgeWeight : "weight"
        ).fromEntry;
        var outputReducer = typeof params.outputReducer === "function" ? params.outputReducer : null;
        var settings = helpers.assign({}, DEFAULT_SETTINGS2, params.settings);
        var validationError = helpers.validateSettings(settings);
        if (validationError)
          throw new Error(
            "graphology-layout-forceatlas2: " + validationError.message
          );
        var matrices = helpers.graphToByteArrays(graph, getEdgeWeight);
        var i;
        for (i = 0; i < iterations; i++)
          iterate(settings, matrices.nodes, matrices.edges);
        if (assign2) {
          helpers.assignLayoutChanges(graph, matrices.nodes, outputReducer);
          return;
        }
        return helpers.collectLayoutChanges(graph, matrices.nodes);
      }
      function inferSettings(graph) {
        var order = typeof graph === "number" ? graph : graph.order;
        return {
          barnesHutOptimize: order > 2e3,
          strongGravityMode: true,
          gravity: 0.05,
          scalingRatio: 10,
          slowDown: 1 + Math.log(order)
        };
      }
      var synchronousLayout = abstractSynchronousLayout.bind(null, false);
      synchronousLayout.assign = abstractSynchronousLayout.bind(null, true);
      synchronousLayout.inferSettings = inferSettings;
      module.exports = synchronousLayout;
    }
  });

  // src/core/router.ts
  var Router = class {
    constructor(loaders) {
      /** The currently visible view. */
      this.currentView = "companies";
      this.loaders = loaders;
    }
    /** Wire the static .nav-link anchors in templates/findata.html. */
    bindNav() {
      document.querySelectorAll(".nav-link").forEach((link) => {
        link.addEventListener("click", (e) => {
          e.preventDefault();
          this.switchView(link.dataset.view);
        });
      });
    }
    /** True when `view` is the visible view (async loads check before rendering). */
    isActive(view) {
      return this.currentView === view;
    }
    switchView(view) {
      document.querySelectorAll(".nav-link").forEach((link) => {
        link.classList.remove("active");
      });
      const activeLink = document.querySelector(`[data-view="${view}"]`);
      if (activeLink) activeLink.classList.add("active");
      document.querySelectorAll(".view-section").forEach((section2) => {
        section2.style.display = "none";
      });
      const section = document.getElementById(`${view}-view`);
      if (!section) {
        throw new Error(`expected element #${view}-view not found in DOM`);
      }
      section.style.display = "block";
      this.currentView = view;
      void this.loaders[view]();
    }
  };

  // src/core/dom.ts
  function getEl(id) {
    const node = document.getElementById(id);
    if (!node) {
      throw new Error(`expected element #${id} not found in DOM`);
    }
    return node;
  }
  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }
  function truncateText(text, maxLength) {
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + "...";
  }
  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB"];
    let value = bytes;
    let unit = -1;
    do {
      value /= 1024;
      unit += 1;
    } while (value >= 1024 && unit < units.length - 1);
    return `${value.toFixed(1)} ${units[unit]}`;
  }

  // src/core/toast.ts
  function showLoading(show) {
    const loading = getEl("loading");
    loading.style.display = show ? "block" : "none";
  }
  function showError(message) {
    const toast = document.createElement("div");
    toast.className = "toast error";
    toast.innerHTML = `
        <i class="fas fa-exclamation-circle"></i>
        <span>${escapeHtml(message)}</span>
    `;
    document.body.appendChild(toast);
    setTimeout(() => {
      toast.remove();
    }, 3e3);
  }
  function showToast(message, kind) {
    const toast = document.createElement("div");
    toast.className = `toast ${kind}`;
    toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3e3);
  }

  // src/core/markdown.ts
  function processRichContent(content) {
    let processedHtml = DOMPurify.sanitize(marked.parse(content));
    const headings = [];
    processedHtml = processedHtml.replace(
      /<h([1-6])[^>]*>(.*?)<\/h[1-6]>/gi,
      (match, level, text) => {
        const id = text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
        headings.push({
          level: parseInt(level, 10),
          text: text.replace(/<[^>]*>/g, ""),
          id
        });
        return `<h${level} id="${id}">${text}</h${level}>`;
      }
    );
    processedHtml = processedHtml.replace(
      /<img([^>]+)src="([^"]+)"([^>]*)>/gi,
      (_match, before, src, after) => {
        const imgId = `img-${Math.random().toString(36).substring(2, 11)}`;
        return `<img${before}src="${src}"${after} class="rich-image" data-img-id="${imgId}" data-lightbox="${escapeAttr(src)}" loading="lazy">`;
      }
    );
    processedHtml = processedHtml.replace(
      /<pre><code class="language-(\w+)">([\s\S]*?)<\/code><\/pre>/gi,
      (_match, lang, code) => {
        const codeId = `code-${Math.random().toString(36).substring(2, 11)}`;
        const highlightedCode = highlightCode(code, lang);
        return `
            <div class="code-block">
                <div class="code-header">
                    <span class="code-language">${lang}</span>
                    <button class="code-copy" data-copy="${codeId}" title="Copy code">
                        <i class="fas fa-copy"></i>
                    </button>
                </div>
                <pre><code id="${codeId}" class="language-${lang}">${highlightedCode}</code></pre>
            </div>
        `;
      }
    );
    processedHtml = processedHtml.replace(
      /<code>([\s\S]*?)<\/code>/gi,
      '<code class="inline-code">$1</code>'
    );
    processedHtml = processedHtml.replace(
      /<table([^>]*)>([\s\S]*?)<\/table>/gi,
      (_match, attributes, tableContent) => {
        return `
            <div class="table-wrapper">
                <table${attributes}>${tableContent}</table>
            </div>
        `;
      }
    );
    processedHtml = processedHtml.replace(
      /<blockquote>([\s\S]*?)<\/blockquote>/gi,
      '<blockquote class="rich-blockquote">$1</blockquote>'
    );
    processedHtml = processExternalContent(processedHtml);
    return {
      html: processedHtml,
      headings
    };
  }
  function highlightCode(code, language) {
    try {
      if (window.hljs) {
        return window.hljs.highlight(code, { language }).value;
      }
    } catch (e) {
      console.warn("Syntax highlighting failed:", e);
    }
    return escapeHtml(code);
  }
  function copyCode(codeId) {
    const codeElement = getEl(codeId);
    if (codeElement) {
      const text = codeElement.textContent;
      navigator.clipboard.writeText(text || "").then(() => {
        showToast("Code copied to clipboard!", "success");
      }).catch((err) => {
        console.error("Failed to copy code:", err);
        showToast("Failed to copy code", "error");
      });
    }
  }
  function openLightbox(imageSrc) {
    const lightbox = getEl("image-lightbox");
    const lightboxImage = getEl("lightbox-image");
    const caption = document.querySelector(".lightbox-caption");
    lightboxImage.src = imageSrc;
    caption.textContent = imageSrc.split("/").pop() || "Image";
    lightbox.style.display = "flex";
    document.body.style.overflow = "hidden";
  }
  function closeLightbox() {
    const lightbox = getEl("image-lightbox");
    lightbox.style.display = "none";
    document.body.style.overflow = "";
  }
  function processExternalContent(html) {
    html = html.replace(
      /https?:\/\/(?:www\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]+)/gi,
      '<div class="video-embed"><iframe src="https://www.youtube.com/embed/$1" frameborder="0" allowfullscreen></iframe></div>'
    );
    html = html.replace(/<a href="([^"]+)"([^>]*)>/gi, (match, href, rest) => {
      const isExternal = href.startsWith("http") && !href.includes(window.location.hostname);
      const externalClass = isExternal ? "external-link" : "";
      const externalIcon = isExternal ? '<i class="fas fa-external-link-alt"></i>' : "";
      return `<a href="${href}"${rest} class="${externalClass}">${externalIcon}`;
    });
    return html;
  }
  function escapeAttr(text) {
    return text.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  }
  var lightboxOpener = openLightbox;
  function wireRichInteractions(root) {
    root.addEventListener("click", (e) => {
      const target = e.target;
      const img = target.closest("img.rich-image[data-lightbox]");
      if (img) {
        lightboxOpener(img.dataset.lightbox || img.src);
        return;
      }
      const copy = target.closest(".code-copy[data-copy]");
      if (copy && copy.dataset.copy) {
        copyCode(copy.dataset.copy);
      }
    });
  }
  function highlightSnippet(snippet) {
    if (!snippet) return "";
    const OPEN = "";
    const CLOSE = "";
    const markedUp = String(snippet).replace(/<mark>/g, OPEN).replace(/<\/mark>/g, CLOSE);
    return escapeHtml(markedUp).replace(/\u0001/g, "<mark>").replace(/\u0002/g, "</mark>");
  }

  // src/core/api.ts
  var ApiError = class extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  };
  function extractErrorMessage(body, fallback) {
    if (body && typeof body === "object" && "error" in body) {
      const err = body.error;
      if (typeof err === "string" && err) return err;
    }
    return fallback;
  }
  async function fetchJson(url, init) {
    const response = await fetch(url);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new ApiError(
        response.status,
        extractErrorMessage(body, response.statusText || `HTTP ${response.status}`)
      );
    }
    return await response.json();
  }
  async function postJson(url) {
    return await fetchJson(url, { method: "POST" });
  }

  // src/core/loadActive.ts
  async function loadActive(opts) {
    try {
      const data = await opts.fetch();
      opts.onFetched?.(data);
      if (opts.isActive()) opts.display(data);
    } catch (error) {
      opts.onError(error);
    }
  }

  // src/views/companies.ts
  var CompaniesView = class {
    constructor(isActive) {
      // --- view + pagination state ---------------------------------------- //
      this.currentLayout = "grid";
      this.currentPage = 0;
      this.pageSize = 20;
      this.totalCount = 0;
      /**
       * 'entities' (default) queries /api/entities by name+tag; 'content'
       * queries the FTS5 /api/search endpoint over note bodies + newsletters.
       */
      this.searchMode = "entities";
      this.filters = {
        search: "",
        sector: "",
        type: "",
        marketcap: ""
      };
      this.isActive = isActive;
    }
    /** Wire the static controls in templates/findata.html (called once at boot). */
    bindEvents() {
      const searchInput = getEl("search-input");
      const clearSearch = getEl("clear-search");
      searchInput.addEventListener("input", (e) => {
        const target = e.target;
        this.filters.search = target.value;
        clearSearch.style.display = target.value ? "block" : "none";
        this.debounceSearch();
      });
      clearSearch.addEventListener("click", () => {
        searchInput.value = "";
        this.filters.search = "";
        clearSearch.style.display = "none";
        this.searchEntities();
      });
      getEl("mode-entities").addEventListener("click", () => {
        this.setSearchMode("entities");
      });
      getEl("mode-content").addEventListener("click", () => {
        this.setSearchMode("content");
      });
      const sectorFilter = getEl("sector-filter");
      sectorFilter.addEventListener("change", (e) => {
        this.filters.sector = e.target.value;
        if (this.isActive()) {
          this.filters.type = "company";
        }
        this.searchEntities();
      });
      const typeFilter = getEl("type-filter");
      typeFilter.addEventListener("change", (e) => {
        this.filters.type = e.target.value;
        this.searchEntities();
      });
      const marketcapFilter = getEl("marketcap-filter");
      marketcapFilter.addEventListener("change", (e) => {
        this.filters.marketcap = e.target.value;
        this.searchEntities();
      });
      getEl("grid-view").addEventListener("click", () => {
        this.setLayout("grid");
      });
      getEl("list-view").addEventListener("click", () => {
        this.setLayout("list");
      });
    }
    debounceSearch() {
      clearTimeout(this.searchTimeout);
      this.searchTimeout = setTimeout(() => {
        this.searchEntities();
      }, 300);
    }
    async loadEntities(resetPage = true) {
      if (resetPage) {
        this.currentPage = 0;
      }
      showLoading(true);
      try {
        await loadActive({
          fetch: async () => {
            const params = new URLSearchParams({
              limit: String(this.pageSize),
              offset: String(this.currentPage * this.pageSize),
              ...this.filters
            });
            if (this.isActive() && !params.has("type")) {
              params.set("type", "company");
            }
            return fetchJson(`/api/entities?${params}`);
          },
          // totalCount tracks the query even when this view is hidden.
          onFetched: (data) => {
            this.totalCount = data.total_count;
          },
          display: (data) => {
            this.displayEntities(data.entities);
            this.updatePagination();
            this.updateCount();
          },
          isActive: this.isActive,
          onError: (error) => {
            console.error("Error loading entities:", error);
            showError("Failed to load entities");
          }
        });
      } finally {
        showLoading(false);
      }
    }
    searchEntities() {
      if (this.searchMode === "content" && this.isActive()) {
        this.performContentSearch();
      } else {
        this.loadEntities();
      }
    }
    setSearchMode(mode) {
      if (mode === this.searchMode) return;
      this.searchMode = mode;
      getEl("mode-entities").classList.toggle("active", mode === "entities");
      getEl("mode-content").classList.toggle("active", mode === "content");
      const filtersApply = mode === "entities";
      document.querySelectorAll(".filters select").forEach((sel) => {
        sel.disabled = !filtersApply;
      });
      this.currentPage = 0;
      this.searchEntities();
    }
    /** Sector-tag handoff from the sectors view (sets state + dropdown). */
    setSectorFilter(sector) {
      this.filters.sector = sector;
      getEl("sector-filter").value = sector;
    }
    async performContentSearch() {
      const q = (this.filters.search || "").trim();
      showLoading(true);
      try {
        const params = new URLSearchParams({
          q,
          limit: String(this.pageSize),
          offset: String(this.currentPage * this.pageSize)
        });
        const response = await fetch(`/api/search?${params}`);
        if (response.status === 503) {
          this.displayContentResults(
            [],
            0,
            "Search index not built. Run: python3 helpers/maintenance/rebuild_note_search.py"
          );
          return;
        }
        if (!response.ok) {
          const err = await response.json().catch(() => ({}));
          this.displayContentResults(
            [],
            0,
            err.error || `Search failed (HTTP ${response.status})`
          );
          return;
        }
        const data = await response.json();
        this.totalCount = data.total_count;
        this.displayContentResults(data.results, data.total_count);
        this.updatePagination();
        this.updateCount();
      } catch (error) {
        console.error("Error in content search:", error);
        showError("Content search failed");
      } finally {
        showLoading(false);
      }
    }
    displayContentResults(results, totalCount, errorMessage = null) {
      const container = getEl("companies-container");
      if (errorMessage) {
        container.innerHTML = `<div class="no-results">${escapeHtml(errorMessage)}</div>`;
        return;
      }
      if (!results || results.length === 0) {
        container.innerHTML = '<div class="no-results">No content matches. Try another term, or switch to Entities mode.</div>';
        return;
      }
      container.innerHTML = "";
      results.forEach((hit) => {
        const card = document.createElement("div");
        card.className = "entity-card content-search-card";
        const docLabel = {
          company: "Company",
          sector: "Sector",
          super_sector: "Super-Sector",
          chatter: "Newsletter (Chatter)",
          points_and_figures: "Newsletter (P&F)",
          plotlines: "Newsletter (Plotlines)"
        };
        const safeSnippet = highlightSnippet(hit.snippet);
        const viewLink = hit.file_path ? `<a href="/entity/${encodeURIComponent(hit.file_path)}" class="btn-primary" target="_blank" rel="noopener noreferrer"><i class="fas fa-external-link-alt"></i> View note</a>` : "";
        card.innerHTML = `
                <div class="card-header">
                    <h3 class="card-title">${escapeHtml(hit.title || "(untitled)")}</h3>
                    <div class="card-type"><span class="doc-badge">${escapeHtml(docLabel[hit.doc_type] || hit.doc_type)}</span></div>
                </div>
                <div class="card-body">
                    ${hit.sector ? `<div class="card-sector"><i class="fas fa-industry"></i><span>${escapeHtml(hit.sector)}</span></div>` : ""}
                    <div class="content-snippet">${safeSnippet}</div>
                </div>
                ${viewLink ? `<div class="card-footer">${viewLink}</div>` : ""}
            `;
        container.appendChild(card);
      });
    }
    displayEntities(entities) {
      const container = getEl("companies-container");
      container.innerHTML = "";
      if (entities.length === 0) {
        container.innerHTML = '<div class="no-results">No companies found</div>';
        return;
      }
      entities.forEach((entity) => {
        const card = this.createEntityCard(entity);
        container.appendChild(card);
      });
    }
    createEntityCard(entity) {
      const card = document.createElement("div");
      card.className = "entity-card";
      const tags = entity.enhanced_tags || [];
      const marketCapTag = tags.find((tag) => tag.startsWith("market_cap/"));
      const geographyTag = tags.find((tag) => tag.startsWith("geography/"));
      card.innerHTML = `
            <div class="card-header">
                <h3 class="card-title">${escapeHtml(entity.name)}</h3>
                <div class="card-type">
                    <i class="fas fa-building"></i>
                    <span>${escapeHtml(entity.entity_type || "company")}</span>
                </div>
            </div>
            <div class="card-body">
                <div class="card-sector">
                    <i class="fas fa-industry"></i>
                    <span>${escapeHtml(entity.sector_classification || "Unknown")}</span>
                </div>
                ${marketCapTag ? `<div class="card-market-cap">
                    <i class="fas fa-chart-line"></i>
                    <span>${escapeHtml(marketCapTag.replace("market_cap/", "").replace("_", " "))}</span>
                </div>` : ""}
                ${geographyTag ? `<div class="card-geography">
                    <i class="fas fa-globe"></i>
                    <span>${escapeHtml(geographyTag.replace("geography/", ""))}</span>
                </div>` : ""}
            </div>
            <div class="card-footer">
                <a href="/entity/${entity.file_path}" class="btn-primary" target="_blank" rel="noopener noreferrer">
                    <i class="fas fa-external-link-alt"></i> View Details
                </a>
            </div>
        `;
      return card;
    }
    setLayout(layout) {
      this.currentLayout = layout;
      document.querySelectorAll(".view-btn").forEach((btn) => {
        btn.classList.remove("active");
      });
      const activeBtn = document.querySelector(`[data-layout="${layout}"]`);
      if (activeBtn) activeBtn.classList.add("active");
      const container = getEl("companies-container");
      container.className = `entities-container ${layout}-layout`;
    }
    updatePagination() {
      const container = getEl("companies-pagination");
      const totalPages = Math.ceil(this.totalCount / this.pageSize);
      if (totalPages <= 1) {
        container.innerHTML = "";
        return;
      }
      let paginationHtml = "";
      if (this.currentPage > 0) {
        paginationHtml += `<button class="page-btn" onclick="viewer.goToPage(${this.currentPage - 1})">Previous</button>`;
      }
      const startPage = Math.max(0, this.currentPage - 2);
      const endPage = Math.min(totalPages - 1, this.currentPage + 2);
      if (startPage > 0) {
        paginationHtml += `<button class="page-btn" onclick="viewer.goToPage(0)">1</button>`;
        if (startPage > 1) {
          paginationHtml += '<span class="page-ellipsis">...</span>';
        }
      }
      for (let i = startPage; i <= endPage; i++) {
        const activeClass = i === this.currentPage ? "active" : "";
        paginationHtml += `<button class="page-btn ${activeClass}" onclick="viewer.goToPage(${i})">${i + 1}</button>`;
      }
      if (endPage < totalPages - 1) {
        if (endPage < totalPages - 2) {
          paginationHtml += '<span class="page-ellipsis">...</span>';
        }
        paginationHtml += `<button class="page-btn" onclick="viewer.goToPage(${totalPages - 1})">${totalPages}</button>`;
      }
      if (this.currentPage < totalPages - 1) {
        paginationHtml += `<button class="page-btn" onclick="viewer.goToPage(${this.currentPage + 1})">Next</button>`;
      }
      container.innerHTML = paginationHtml;
    }
    /** Pagination entry point — invoked by inline onclick in page buttons. */
    goToPage(page) {
      this.currentPage = page;
      if (this.searchMode === "content" && this.isActive()) {
        this.performContentSearch();
      } else {
        this.loadEntities(false);
      }
    }
    updateCount() {
      const countLabel = getEl("companies-count");
      const start = this.currentPage * this.pageSize + 1;
      const end = Math.min(start + this.pageSize - 1, this.totalCount);
      countLabel.textContent = `${start}-${end} of ${this.totalCount}`;
    }
  };

  // src/views/sectors.ts
  var SectorsView = class {
    constructor(isActive, onSectorPicked) {
      this.isActive = isActive;
      this.onSectorPicked = onSectorPicked;
    }
    async load() {
      await loadActive({
        fetch: () => fetchJson("/api/sectors"),
        // Unguarded: the sector-filter dropdown lives in the companies
        // view but is populated from here — must run even when this
        // view is not visible.
        onFetched: (data) => {
          const sectorFilter = getEl("sector-filter");
          data.classifications.forEach((sector) => {
            const option = document.createElement("option");
            option.value = sector;
            option.textContent = sector;
            sectorFilter.appendChild(option);
          });
        },
        display: (data) => this.displaySectors(data),
        isActive: this.isActive,
        onError: (error) => console.error("Error loading sectors:", error)
      });
    }
    displaySectors(data) {
      const container = getEl("sectors-container");
      container.innerHTML = "";
      const classificationsDiv = document.createElement("div");
      classificationsDiv.className = "sector-classifications";
      classificationsDiv.innerHTML = "<h3>Sector Classifications</h3>";
      const grid = document.createElement("div");
      grid.className = "sector-tags";
      data.classifications.forEach((sector) => {
        const tag = document.createElement("span");
        tag.className = "sector-tag";
        tag.textContent = sector;
        tag.addEventListener("click", () => {
          this.onSectorPicked(sector);
        });
        grid.appendChild(tag);
      });
      classificationsDiv.appendChild(grid);
      container.appendChild(classificationsDiv);
      if (data.sector_entities.length > 0) {
        const entitiesDiv = document.createElement("div");
        entitiesDiv.className = "sector-entities";
        entitiesDiv.innerHTML = "<h3>Sector Analysis</h3>";
        data.sector_entities.forEach((sector) => {
          const card = this.createSectorCard(sector);
          entitiesDiv.appendChild(card);
        });
        container.appendChild(entitiesDiv);
      }
      getEl("sectors-count").textContent = `${data.classifications.length} classifications`;
    }
    createSectorCard(sector) {
      const card = document.createElement("div");
      card.className = "sector-card";
      card.innerHTML = `
            <div class="card-header">
                <h3 class="card-title">${escapeHtml(sector.name)}</h3>
                <div class="card-type">
                    <i class="fas fa-industry"></i>
                    <span>Sector</span>
                </div>
            </div>
            <div class="card-body">
                <div class="card-content">
                    ${truncateText(sector.content, 150)}
                </div>
            </div>
            <div class="card-footer">
                <a href="/entity/${encodeURIComponent(sector.file_path)}" class="btn-primary" target="_blank" rel="noopener noreferrer">
                    <i class="fas fa-external-link-alt"></i> Read Analysis
                </a>
            </div>
        `;
      return card;
    }
  };

  // src/views/stats.ts
  var StatsView = class {
    constructor(isActive) {
      this.isActive = isActive;
    }
    async load() {
      await loadActive({
        fetch: () => fetchJson("/api/stats"),
        display: (data) => this.displayStats(data),
        isActive: this.isActive,
        onError: (error) => console.error("Error loading stats:", error)
      });
      await loadActive({
        fetch: () => fetchJson("/api/graph/stats"),
        display: (data) => this.displayGraphStats(data),
        isActive: this.isActive,
        onError: (error) => {
          console.error("Error loading graph stats:", error);
          this.displayGraphStatsError();
        }
      });
    }
    displayStats(data) {
      const container = getEl("stats-container");
      container.innerHTML = "";
      const totalCard = this.createStatCard(
        "Total Entities",
        data.total_entities,
        "fas fa-database",
        "primary"
      );
      container.appendChild(totalCard);
      const typesCard = this.createStatCard(
        "Entity Types",
        Object.keys(data.entity_counts).length,
        "fas fa-tags",
        "secondary"
      );
      container.appendChild(typesCard);
      const sectorsCard = this.createStatCard(
        "Sectors",
        Object.keys(data.top_sectors).length,
        "fas fa-industry",
        "success"
      );
      container.appendChild(sectorsCard);
      const marketCapCard = this.createStatCard(
        "Market Cap Categories",
        Object.keys(data.market_cap_counts).length,
        "fas fa-chart-line",
        "warning"
      );
      container.appendChild(marketCapCard);
      const breakdownSection = document.createElement("div");
      breakdownSection.className = "stats-breakdown";
      breakdownSection.appendChild(
        this.createBreakdownCard("Entity Types", data.entity_counts, "entity_type")
      );
      breakdownSection.appendChild(
        this.createBreakdownCard("Top Sectors", data.top_sectors, "sector")
      );
      breakdownSection.appendChild(
        this.createBreakdownCard(
          "Market Cap Distribution",
          data.market_cap_counts,
          "market_cap"
        )
      );
      container.appendChild(breakdownSection);
    }
    /** Full graph-stats block for the Statistics view (from /api/graph/stats). */
    displayGraphStats(data) {
      const container = getEl("stats-container");
      if (!container) return;
      const section = document.createElement("div");
      section.className = "stats-graph-block";
      section.innerHTML = `
            <div class="stats-graph-header">
                <h3><i class="fas fa-project-diagram"></i> Graph Statistics</h3>
                ${data.structure ? '<span class="stats-graph-meta">via Onager graph metrics</span>' : ""}
            </div>
            <div class="stats-graph-cards">${this._graphStatsCards(data)}</div>
        `;
      if (data.structure) {
        const structure = data.structure;
        const items = [
          ["Density", structure.density],
          ["Diameter", structure.diameter],
          ["Radius", structure.radius],
          ["Avg path length", structure.avg_path_length],
          ["Transitivity", structure.transitivity],
          ["Triangles", structure.triangles],
          ["Avg clustering", structure.avg_clustering],
          ["Assortativity", structure.assortativity]
        ];
        const metrics = document.createElement("div");
        metrics.className = "breakdown-card stats-graph-structure";
        metrics.innerHTML = `<h4>Structure</h4><div class="breakdown-items">` + items.map(
          ([label, v]) => `
                    <div class="breakdown-item">
                        <span class="breakdown-label">${escapeHtml(label)}</span>
                        <span class="breakdown-value">${v === null ? "\u2014" : typeof v === "number" ? v.toFixed(4) : escapeHtml(String(v))}</span>
                    </div>`
        ).join("") + `</div>`;
        section.appendChild(metrics);
      } else {
        const note = document.createElement("div");
        note.className = "hint";
        note.textContent = "Structure metrics unavailable (graph analysis layer not connected).";
        section.appendChild(note);
      }
      const byType = data.edges.by_type || {};
      const sorted = {};
      Object.keys(byType).sort((a, b) => byType[b] - byType[a]).forEach((k) => {
        sorted[k] = byType[k];
      });
      section.appendChild(this.createBreakdownCard("Edge Types", sorted, "edge_type"));
      container.appendChild(section);
    }
    /** Inner stat cards for the graph block (edges + entities + sectors). */
    _graphStatsCards(data) {
      const hy = data.hygiene || {};
      const stale = data.staleness?.stale;
      const staleColor = stale ? "#e63946" : "#2a9d8f";
      const staleLabel = stale ? "Stale" : "Fresh";
      return `
            <div class="stat-card stat-primary">
                <div class="stat-content"><h3>${data.edges.total.toLocaleString()}</h3><p>Total Edges</p></div>
            </div>
            <div class="stat-card stat-secondary">
                <div class="stat-content"><h3>${Object.keys(data.edges.by_type || {}).length}</h3><p>Edge Types</p></div>
            </div>
            <div class="stat-card stat-secondary">
                <div class="stat-content"><h3>${data.entities.total.toLocaleString()}</h3><p>Graph Entities</p></div>
            </div>
            <div class="stat-card stat-secondary">
                <div class="stat-content"><h3>${data.sectors?.count ?? 0}</h3><p>Company Sectors</p></div>
            </div>
            <div class="stat-card stat-secondary">
                <div class="stat-content"><h3>${data.sectors?.top?.[0]?.sector ?? "\u2014"}</h3><p>Top Sector</p></div>
            </div>
            <div class="stat-card stat-secondary">
                <div class="stat-content"><h3 style="color:${staleColor}">${staleLabel}</h3><p>Data Staleness</p></div>
            </div>`;
    }
    /** Degraded fallback when /api/graph/stats is unreachable. */
    displayGraphStatsError() {
      const container = getEl("stats-container");
      if (!container) return;
      const note = document.createElement("div");
      note.className = "hint";
      note.textContent = "Graph statistics could not be loaded.";
      container.appendChild(note);
    }
    createStatCard(title, value, icon, theme) {
      const card = document.createElement("div");
      card.className = `stat-card stat-${theme}`;
      card.innerHTML = `
            <div class="stat-icon">
                <i class="${icon}"></i>
            </div>
            <div class="stat-content">
                <h3>${value.toLocaleString()}</h3>
                <p>${title}</p>
            </div>
        `;
      return card;
    }
    createBreakdownCard(title, data, type) {
      const card = document.createElement("div");
      card.className = "breakdown-card";
      const total = Object.values(data).reduce((a, b) => a + b, 0);
      let itemsHtml = "";
      Object.entries(data).forEach(([key, value]) => {
        const percentage = total > 0 ? (value / total * 100).toFixed(1) : "0.0";
        itemsHtml += `
                <div class="breakdown-item">
                    <span class="breakdown-label">${escapeHtml(this.formatLabel(key, type))}</span>
                    <span class="breakdown-value">${value}</span>
                    <span class="breakdown-percentage">${percentage}%</span>
                </div>
            `;
      });
      card.innerHTML = `
            <h4>${title}</h4>
            <div class="breakdown-items">
                ${itemsHtml}
            </div>
        `;
      return card;
    }
    formatLabel(key, type) {
      if (type === "market_cap") {
        return key.replace("_", " ").replace(/\b\w/g, (l) => l.toUpperCase());
      }
      if (type === "edge_type") {
        return key.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase());
      }
      return key;
    }
  };

  // src/core/reader.ts
  var SERIES_LABELS = {
    The_Chatter: "The Chatter",
    Points_And_Figures: "Points & Figures",
    The_PlotLines: "The Plotlines"
  };
  var WIKILINK_RE = /\[\[([^\[\]|]+?)(?:#[^\[\]|]*)?(?:\|([^\[\]]+?))?\]\]/g;
  var CHIP_KEYS = ["ticker", "sector", "industry", "market_cap", "created", "last_modified"];
  function seriesLabel(filePath) {
    return filePath ? SERIES_LABELS[filePath.split("/")[1]] : void 0;
  }
  function readerTitle(entity) {
    return fmString(entity.frontmatter, "title") ?? entity.name.replace(/_/g, " ");
  }
  function editionBits(entity) {
    const fm = entity.frontmatter;
    const bits = [];
    const publisher = fmPublisher(fm);
    if (publisher) bits.push(escapeHtml(publisher));
    const generated = fmGeneratedAt(fm);
    if (generated) bits.push(`generated ${escapeHtml(generated)}`);
    const stale = fmScalar(fm, "stale_after");
    if (stale) bits.push(`fresh through ${escapeHtml(stale)}`);
    return bits;
  }
  function chipSpans(entity) {
    const fm = entity.frontmatter;
    const chips = [
      `<span class="fm-chip fm-type">${escapeHtml(entity.entity_type.replace(/_/g, " "))}</span>`
    ];
    for (const key of CHIP_KEYS) {
      const value = fmScalar(fm, key);
      if (value) {
        chips.push(
          `<span class="fm-chip"><b>${escapeHtml(key.replace(/_/g, " "))}</b>${escapeHtml(value)}</span>`
        );
      }
    }
    return chips.join("");
  }
  function buildWikilinkIndex(entities) {
    const index = /* @__PURE__ */ new Map();
    for (const entity of entities) {
      if (!entity.file_path) continue;
      const stem = (entity.file_path.split("/").pop() || "").replace(/\.md$/i, "");
      if (stem && !index.has(stem)) index.set(stem, entity.file_path);
      if (entity.name && !index.has(entity.name)) index.set(entity.name, entity.file_path);
    }
    return index;
  }
  function linkifyWikilinks(root, index, hrefFor) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        let parent = node.parentElement;
        while (parent && parent !== root) {
          const tag = parent.nodeName;
          if (tag === "CODE" || tag === "PRE" || tag === "A" || tag === "SCRIPT") {
            return NodeFilter.FILTER_REJECT;
          }
          parent = parent.parentElement;
        }
        return node.nodeValue && node.nodeValue.includes("[[") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });
    const targets = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      targets.push(n);
    }
    for (const textNode of targets) {
      const text = textNode.nodeValue || "";
      WIKILINK_RE.lastIndex = 0;
      if (!WIKILINK_RE.test(text)) continue;
      const fragment = document.createDocumentFragment();
      let cursor = 0;
      let match;
      WIKILINK_RE.lastIndex = 0;
      while ((match = WIKILINK_RE.exec(text)) !== null) {
        if (match.index > cursor) {
          fragment.appendChild(document.createTextNode(text.slice(cursor, match.index)));
        }
        const target = (match[1] || "").trim();
        const label = (match[2] || "").trim() || target;
        const href = index.get(target);
        if (href) {
          const resolved = hrefFor(href);
          const anchor = document.createElement("a");
          anchor.className = "wikilink";
          anchor.href = resolved.href;
          if (resolved.datasetHref !== void 0) {
            anchor.dataset.href = resolved.datasetHref;
          }
          anchor.title = href;
          anchor.textContent = label;
          fragment.appendChild(anchor);
        } else {
          const miss = document.createElement("span");
          miss.className = "wikilink wikilink-miss";
          miss.title = "unresolved note";
          miss.textContent = label;
          fragment.appendChild(miss);
        }
        cursor = match.index + match[0].length;
      }
      if (cursor < text.length) {
        fragment.appendChild(document.createTextNode(text.slice(cursor)));
      }
      textNode.replaceWith(fragment);
    }
  }
  function fmString(fm, key) {
    const v = fm[key];
    return typeof v === "string" && v.trim() ? v : null;
  }
  function fmScalar(fm, key) {
    const v = fmString(fm, key);
    if (v === null) return null;
    return /^\d{4}-\d{2}-\d{2}T/.test(v) ? v.slice(0, 10) : v;
  }
  function fmGeneratedAt(fm) {
    const g = fm.generated;
    if (g && typeof g === "object" && "at" in g) {
      const at = g.at;
      if (typeof at === "string" && at) return at.slice(0, 10);
    }
    return null;
  }
  function fmPublisher(fm) {
    const tags = Array.isArray(fm.tags) ? fm.tags.filter((t) => typeof t === "string") : [];
    for (const tag of tags) {
      if (tag.startsWith("publisher/")) {
        return tag.slice("publisher/".length).replace(/\b\w/g, (c) => c.toUpperCase());
      }
    }
    return null;
  }

  // src/views/docs.ts
  var DOCTYPE_LABELS = {
    company: "company",
    sector: "sector",
    super_sector: "super sector",
    chatter: "chatter",
    points_and_figures: "P&F",
    plotlines: "plotlines"
  };
  var READSIZE_KEY = "findata.docs.readsize";
  var FOCUS_KEY = "findata.docs.focus";
  var DocsView = class {
    constructor(isActive) {
      // --- docs-tab state --------------------------------------------------- //
      this.activePath = null;
      /** Which collection the sidebar shows. */
      this.collection = "docs";
      /** Vault entities with notes (cached; null until first vault open). */
      this.vaultEntities = null;
      /** stem/name → repo-relative file_path, the wikilink resolver. */
      this.wikilinks = null;
      this.isActive = isActive;
    }
    /** Wire the static docs controls + lightbox dismissers (once at boot). */
    bindEvents() {
      getEl("close-lightbox").addEventListener("click", () => {
        closeLightbox();
      });
      getEl("image-lightbox").addEventListener("click", (e) => {
        if (e.target.id === "image-lightbox") {
          closeLightbox();
        }
      });
      const docsSearch = getEl("docs-search");
      const docsClear = getEl("docs-search-clear");
      docsSearch.addEventListener("input", (e) => {
        const target = e.target;
        docsClear.style.display = target.value ? "block" : "none";
        this.debounceDocsSearch();
      });
      docsClear.addEventListener("click", () => {
        docsSearch.value = "";
        docsClear.style.display = "none";
        this.loadCatalog();
      });
      getEl("docs-reset").addEventListener("click", () => {
        docsSearch.value = "";
        docsClear.style.display = "none";
        this.loadCatalog();
      });
      document.querySelectorAll(".collection-tab").forEach((tab) => {
        tab.addEventListener("click", () => {
          const c = tab.dataset.collection;
          if (c !== this.collection) this.setCollection(c);
        });
      });
      getEl("hybrid-search").addEventListener("change", () => {
        if (getEl("docs-search").value.trim()) {
          this.debounceDocsSearch();
        }
      });
      getEl("docs-content-pane").addEventListener("click", (e) => {
        const anchor = e.target.closest("a.wikilink");
        if (!anchor) return;
        e.preventDefault();
        const href = anchor.dataset.href;
        if (href) void this.openNote(href);
      });
      const view = getEl("docs-view");
      const applyReadSize = (size) => {
        view.dataset.readsize = size;
        document.querySelectorAll(".readsize-btn").forEach((b) => {
          b.classList.toggle("active", b.dataset.readsize === size);
        });
        try {
          localStorage.setItem(READSIZE_KEY, size);
        } catch {
        }
      };
      document.querySelectorAll(".readsize-btn").forEach((btn) => {
        btn.addEventListener("click", () => applyReadSize(btn.dataset.readsize || "m"));
      });
      const focusToggle = getEl("docs-focus-toggle");
      const applyFocus = (on) => {
        view.classList.toggle("focus-mode", on);
        focusToggle.classList.toggle("active", on);
        try {
          localStorage.setItem(FOCUS_KEY, on ? "1" : "0");
        } catch {
        }
      };
      focusToggle.addEventListener(
        "click",
        () => applyFocus(!view.classList.contains("focus-mode"))
      );
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && view.classList.contains("focus-mode") && this.isActive()) {
          applyFocus(false);
        }
      });
      try {
        const savedSize = localStorage.getItem(READSIZE_KEY);
        applyReadSize(
          savedSize === "s" || savedSize === "m" || savedSize === "l" ? savedSize : "m"
        );
        if (localStorage.getItem(FOCUS_KEY) === "1") applyFocus(true);
      } catch {
        applyReadSize("m");
      }
    }
    // --- collection switching ---------------------------------------------- //
    setCollection(c) {
      this.collection = c;
      document.querySelectorAll(".collection-tab").forEach((tab) => {
        const active = tab.dataset.collection === c;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
      });
      getEl("hybrid-toggle").style.display = c === "vault" ? "flex" : "none";
      const search = getEl("docs-search");
      search.value = "";
      search.placeholder = c === "vault" ? "Search every vault note (FTS)..." : "Search design docs, proposals, archives...";
      getEl("docs-search-clear").style.display = "none";
      void this.loadCatalog();
    }
    // --- catalog ----------------------------------------------------------- //
    /** Load the active collection's full catalog into the sidebar. */
    async loadCatalog() {
      this.activePath = null;
      this.hideContentPane();
      if (this.collection === "vault") {
        try {
          const entities = await this.ensureVault();
          this.renderVaultList(entities);
          getEl("docs-count").textContent = `${entities.length.toLocaleString()} notes`;
        } catch (error) {
          console.error("Error loading vault:", error);
          getEl("docs-list").innerHTML = '<div class="no-results">Could not load the vault index.</div>';
        }
        return;
      }
      try {
        const data = await fetchJson("/api/docs");
        this.renderGroups([
          {
            label: null,
            rows: data.docs.map((d) => ({
              path: d.path,
              title: d.title,
              sub: d.section || null,
              chip: null,
              snippet: "",
              sim: null
            }))
          }
        ]);
        getEl("docs-count").textContent = `${data.docs.length} documents`;
      } catch (error) {
        console.error("Error loading docs:", error);
        getEl("docs-list").innerHTML = '<div class="no-results">Could not load the document catalog.</div>';
      }
    }
    /** Fetch the vault entity list once; also builds the wikilink index. */
    async ensureVault() {
      if (this.vaultEntities) return this.vaultEntities;
      const data = await fetchJson("/api/entities?limit=5000");
      const withNotes = data.entities.filter((e) => e.file_path);
      this.vaultEntities = withNotes;
      this.wikilinks = buildWikilinkIndex(withNotes);
      return withNotes;
    }
    /** Grouped vault listing: supers → sectors → editions by series → companies by sector. */
    renderVaultList(entities) {
      const byType = (t) => entities.filter((e) => e.entity_type === t);
      const groups = [];
      groups.push({
        label: "Super Sectors",
        rows: byType("super_sector").map(rowForTyped)
      });
      groups.push({
        label: "Sectors",
        rows: byType("sector").map(rowForTyped)
      });
      const editions = byType("edition");
      for (const [dir, label] of Object.entries(SERIES_LABELS)) {
        const rows = editions.filter((e) => (e.file_path || "").split("/")[1] === dir).map(rowForTyped).sort((a, b) => a.title.localeCompare(b.title));
        if (rows.length) groups.push({ label: `${label} (${rows.length})`, rows });
      }
      const sectorGroups = /* @__PURE__ */ new Map();
      for (const company of byType("company")) {
        const key = company.sector_classification || "Unclassified";
        sectorGroups.set(key, [...sectorGroups.get(key) || [], company]);
      }
      for (const key of [...sectorGroups.keys()].sort((a, b) => a.localeCompare(b))) {
        const rows = (sectorGroups.get(key) || []).map(rowForTyped).sort((a, b) => a.title.localeCompare(b.title));
        groups.push({ label: `${key} (${rows.length})`, rows });
      }
      this.renderGroups(groups.filter((g) => g.rows.length > 0));
      function rowForTyped(e) {
        const sub = e.entity_type === "edition" ? SERIES_LABELS[(e.file_path || "").split("/")[1]] || null : e.sector_classification;
        return {
          path: e.file_path,
          title: e.name.replace(/_/g, " "),
          sub,
          chip: null,
          snippet: "",
          sim: null
        };
      }
    }
    // --- search ------------------------------------------------------------ //
    debounceDocsSearch() {
      clearTimeout(this.docsSearchTimeout);
      this.docsSearchTimeout = setTimeout(() => {
        void this.runSearch();
      }, 300);
    }
    /** Search the ACTIVE collection's corpus and render the hits. */
    async runSearch() {
      const query = getEl("docs-search").value.trim();
      this.activePath = null;
      this.hideContentPane();
      if (!query) {
        void this.loadCatalog();
        return;
      }
      if (this.collection === "vault") {
        await this.runVaultSearch(query);
        return;
      }
      try {
        const url = `/api/docs/search?q=${encodeURIComponent(query)}`;
        const data = await fetchJson(url);
        this.renderGroups([
          {
            label: null,
            rows: data.results.map((r) => ({
              path: r.path,
              title: r.title,
              // Prefer the matched section's own title (deep-link
              // context) over the bare directory; anchor rides the chip.
              sub: r.section_title || r.section || null,
              chip: r.anchor !== null && r.anchor !== void 0 ? `L${r.anchor}` : null,
              snippet: r.snippet,
              sim: r.similarity ?? null
            }))
          }
        ]);
        const total = data.results.length;
        const mode = data.mode ? ` \xB7 ${data.mode}` : "";
        const stale = data.stale ? " \xB7 stale (scan)" : "";
        getEl("docs-count").textContent = total === 0 ? `No matches${stale}` : `${total} match${total === 1 ? "" : "es"}${mode}${stale}`;
      } catch (error) {
        console.error("Error searching docs:", error);
        getEl("docs-list").innerHTML = '<div class="no-results">Search failed. Try again.</div>';
      }
    }
    /** FTS (optionally hybrid) search over every findata/ note body. */
    async runVaultSearch(query) {
      const hybrid = getEl("hybrid-search").checked;
      const url = `/api/search?q=${encodeURIComponent(query)}&limit=50${hybrid ? "&hybrid=1" : ""}`;
      try {
        const data = await fetchJson(url);
        this.renderGroups([
          {
            label: null,
            rows: data.results.map((r) => ({
              path: r.file_path,
              // note_search titles are stems for companies — prettify.
              title: (r.title ?? "(untitled)").replace(/_/g, " "),
              sub: r.sector,
              chip: DOCTYPE_LABELS[r.doc_type] || r.doc_type,
              snippet: r.snippet,
              sim: hybrid ? r.similarity : null
            }))
          }
        ]);
        getEl("docs-count").textContent = `${data.total_count.toLocaleString()} match${data.total_count === 1 ? "" : "es"}` + (hybrid ? " \xB7 hybrid" : "");
      } catch (error) {
        console.error("Error searching vault:", error);
        getEl("docs-list").innerHTML = `<div class="no-results">Search failed: ${escapeHtml(
          error instanceof Error ? error.message : "unknown error"
        )}</div>`;
      }
    }
    // --- sidebar rendering -------------------------------------------------- //
    /** Render grouped rows; group headers are mono section labels. */
    renderGroups(groups) {
      const list = getEl("docs-list");
      list.innerHTML = "";
      const rowCount = groups.reduce((n, g) => n + g.rows.length, 0);
      if (rowCount === 0) {
        list.innerHTML = '<div class="no-results">No documents match.</div>';
        return;
      }
      for (const group of groups) {
        if (group.label) {
          const head = document.createElement("div");
          head.className = "docs-group-h";
          head.textContent = group.label;
          list.appendChild(head);
        }
        for (const item of group.rows) {
          list.appendChild(this.buildRow(item));
        }
      }
    }
    /** Kept for the S2 public surface (flat lists = one label-less group). */
    renderList(items) {
      this.renderGroups([
        {
          label: null,
          rows: items.map((i) => ({
            path: i.path,
            title: i.title,
            sub: i.section || null,
            chip: null,
            snippet: i.snippet,
            sim: null
          }))
        }
      ]);
    }
    buildRow(item) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "docs-row";
      row.dataset.path = item.path;
      const chip = item.chip ? `<span class="doctype-chip">${escapeHtml(item.chip)}</span>` : "";
      const sub = item.sub ? `<span class="docs-row-section">${escapeHtml(item.sub)}</span>` : "";
      const snippet = item.snippet ? `<div class="docs-row-snippet">${highlightSnippet(item.snippet)}</div>` : "";
      const sim = item.sim != null ? `<span class="docs-row-sim">${(item.sim * 100).toFixed(0)}%</span>` : "";
      row.innerHTML = `
            <span class="docs-row-title">${chip}${escapeHtml(item.title)}</span>
            ${sub}${sim}${snippet}
        `;
      row.addEventListener("click", () => {
        if (this.collection === "vault") void this.openNote(item.path);
        else void this.openDoc(item.path);
      });
      return row;
    }
    markActiveRow(path) {
      document.querySelectorAll(".docs-row").forEach((row) => {
        row.classList.toggle("active", row.dataset.path === path);
      });
    }
    // --- reader ------------------------------------------------------------- //
    /** Fetch + render one doc/ file (raw markdown/text, desk catalog source). */
    async openDoc(path) {
      this.activePath = path;
      this.markActiveRow(path);
      try {
        const url = `/api/docs/content?path=${encodeURIComponent(path)}`;
        const data = await fetchJson(url);
        const { html, headings } = processRichContent(data.content);
        getEl("docs-content-empty").style.display = "none";
        const pane = getEl("docs-content-pane");
        pane.style.display = "block";
        pane.innerHTML = `
                <div class="reader-grid">
                    <div class="reader-main">
                        <header class="docs-article-header">
                            <h3>${escapeHtml(data.title)}</h3>
                            <div class="docs-article-meta">
                                <span>${escapeHtml(data.path)}</span>
                                <span>${escapeHtml(data.section || "top-level")}</span>
                                <span>${formatBytes(data.size_bytes)}</span>
                            </div>
                        </header>
                        <div class="docs-article-body">${html}</div>
                    </div>
                    <aside class="reader-rail">
                        ${headings.length > 1 ? this.renderToc(headings) : ""}
                    </aside>
                </div>
            `;
        wireRichInteractions(pane);
      } catch (error) {
        console.error("Error opening doc:", error);
        this.readerError();
      }
    }
    /** Fetch + render one vault note (paper register, wikilinks, related rail). */
    async openNote(filePath) {
      this.activePath = filePath;
      this.markActiveRow(filePath);
      void this.ensureVault().catch(() => void 0);
      try {
        const url = `/api/entity/${encodeURIComponent(filePath)}`;
        const entity = await fetchJson(url);
        const fm = entity.frontmatter;
        const isEdition = entity.entity_type === "edition" || fmString(fm, "type") === "newsletter";
        const { html, headings } = processRichContent(entity.content);
        getEl("docs-content-empty").style.display = "none";
        const pane = getEl("docs-content-pane");
        pane.style.display = "block";
        pane.innerHTML = `
                <div class="reader-grid">
                    <div class="reader-main">
                        ${isEdition ? this.renderMasthead(entity) : this.renderChips(entity)}
                        <div class="docs-article-body">${html}</div>
                    </div>
                    <aside class="reader-rail">
                        ${headings.length > 1 ? this.renderToc(headings) : ""}
                        <div class="reader-related" id="reader-related"></div>
                    </aside>
                </div>
            `;
        wireRichInteractions(pane);
        if (this.wikilinks) {
          linkifyWikilinks(pane, this.wikilinks, (href) => ({
            href: "#",
            datasetHref: href
          }));
        }
        void this.loadRelatedRail(entity, isEdition);
      } catch (error) {
        console.error("Error opening note:", error);
        this.readerError();
      }
    }
    readerError() {
      getEl("docs-content-pane").style.display = "none";
      getEl("docs-content-empty").style.display = "block";
      getEl("docs-content-empty").innerHTML = '<p class="error">Could not load this document.</p>';
    }
    /** Edition masthead: publication / issue / provenance between double rules. */
    renderMasthead(entity) {
      const series = seriesLabel(entity.file_path);
      const title = readerTitle(entity);
      const bits = editionBits(entity);
      return `
            <header class="edition-masthead">
                <div class="masthead-pub">${escapeHtml(series || "Newsletter")}</div>
                <h3 class="masthead-title">${escapeHtml(title)}</h3>
                ${bits.length ? `<div class="masthead-meta">${bits.join(' <span class="dot">\xB7</span> ')}</div>` : ""}
            </header>
        `;
    }
    /** Non-edition header: title + frontmatter chips in the mono data voice. */
    renderChips(entity) {
      const title = readerTitle(entity);
      return `
            <header class="docs-article-header">
                <h3>${escapeHtml(title)}</h3>
                <div class="fm-chips">${chipSpans(entity)}</div>
                <div class="docs-article-meta">
                    <span>${escapeHtml(entity.file_path || entity.name)}</span>
                </div>
            </header>
        `;
    }
    // --- related rail ---------------------------------------------------------- //
    /** Similar-notes + (editions) featured-companies rail content. */
    async loadRelatedRail(entity, isEdition) {
      const mount = document.getElementById("reader-related");
      if (!mount || !entity.file_path) return;
      const parts = [];
      try {
        const similar = await fetchJson(
          `/api/graph/similar/${encodeURIComponent(entity.file_path)}?k=6`
        );
        if (similar.neighbors.length) {
          parts.push('<h4><i class="fas fa-clone"></i> Similar notes</h4>');
          parts.push(...similar.neighbors.map((n) => this.relatedRow(n)));
        }
      } catch {
      }
      if (isEdition) {
        const stem = (entity.file_path.split("/").pop() || "").replace(/\.md$/i, "");
        try {
          const companies = await fetchJson(
            `/api/graph/edition_companies?edition=${encodeURIComponent(stem)}&k=8`
          );
          if (companies.companies.length) {
            parts.push(
              '<h4><i class="fas fa-building"></i> Companies in this edition</h4>'
            );
            parts.push(...companies.companies.map((n) => this.relatedRow(n)));
          }
        } catch {
        }
      }
      mount.innerHTML = parts.length ? parts.join("") : '<p class="hint">No related notes.</p>';
      mount.querySelectorAll("[data-note]").forEach((el) => {
        el.addEventListener("click", () => {
          const note = el.dataset.note;
          if (note) void this.openNote(note);
        });
      });
    }
    relatedRow(n) {
      const pct = Math.round(n.similarity * 100);
      return `
            <button type="button" class="related-row" data-note="${escapeHtml(n.file_path)}"
                    title="${escapeHtml(n.file_path)}">
                <span class="related-title">${escapeHtml(n.title)}</span>
                <span class="related-sim"><span class="related-bar"><span
                    class="bar-fill" style="width:${pct}%"></span></span>${pct}%</span>
            </button>
        `;
    }
    // --- shared helpers --------------------------------------------------------- //
    /** Simple TOC linking the <h1..h6 id> headings marked.js produces. */
    renderToc(headings) {
      const items = headings.map(
        (h) => `<li class="toc-${h.level}"><a href="#${encodeURIComponent(h.id)}">${escapeHtml(h.text)}</a></li>`
      ).join("");
      return `<nav class="docs-toc"><h4>On this page</h4><ul>${items}</ul></nav>`;
    }
    /** Reset the reader pane to its empty state. */
    hideContentPane() {
      getEl("docs-content-pane").style.display = "none";
      getEl("docs-content-pane").innerHTML = "";
      const empty = getEl("docs-content-empty");
      empty.style.display = "block";
      empty.innerHTML = `
            <i class="fas fa-book-open"></i>
            <p>Select a document to read it here.</p>
            <p class="hint">Browse the doc/ and vault collections, or search \u2014
               flip on hybrid for semantic rerank.</p>
        `;
    }
  };

  // src/views/graphRenderer.ts
  var import_graphology = __toESM(require_graphology_umd_min());

  // node_modules/sigma/dist/inherits-d1a1e29b.esm.js
  function _toPrimitive(t, r) {
    if ("object" != typeof t || !t) return t;
    var e = t[Symbol.toPrimitive];
    if (void 0 !== e) {
      var i = e.call(t, r || "default");
      if ("object" != typeof i) return i;
      throw new TypeError("@@toPrimitive must return a primitive value.");
    }
    return ("string" === r ? String : Number)(t);
  }
  function _toPropertyKey(t) {
    var i = _toPrimitive(t, "string");
    return "symbol" == typeof i ? i : i + "";
  }
  function _classCallCheck(a, n) {
    if (!(a instanceof n)) throw new TypeError("Cannot call a class as a function");
  }
  function _defineProperties(e, r) {
    for (var t = 0; t < r.length; t++) {
      var o = r[t];
      o.enumerable = o.enumerable || false, o.configurable = true, "value" in o && (o.writable = true), Object.defineProperty(e, _toPropertyKey(o.key), o);
    }
  }
  function _createClass(e, r, t) {
    return r && _defineProperties(e.prototype, r), t && _defineProperties(e, t), Object.defineProperty(e, "prototype", {
      writable: false
    }), e;
  }
  function _getPrototypeOf(t) {
    return _getPrototypeOf = Object.setPrototypeOf ? Object.getPrototypeOf.bind() : function(t2) {
      return t2.__proto__ || Object.getPrototypeOf(t2);
    }, _getPrototypeOf(t);
  }
  function _isNativeReflectConstruct() {
    try {
      var t = !Boolean.prototype.valueOf.call(Reflect.construct(Boolean, [], function() {
      }));
    } catch (t2) {
    }
    return (_isNativeReflectConstruct = function() {
      return !!t;
    })();
  }
  function _assertThisInitialized(e) {
    if (void 0 === e) throw new ReferenceError("this hasn't been initialised - super() hasn't been called");
    return e;
  }
  function _possibleConstructorReturn(t, e) {
    if (e && ("object" == typeof e || "function" == typeof e)) return e;
    if (void 0 !== e) throw new TypeError("Derived constructors may only return object or undefined");
    return _assertThisInitialized(t);
  }
  function _callSuper(t, o, e) {
    return o = _getPrototypeOf(o), _possibleConstructorReturn(t, _isNativeReflectConstruct() ? Reflect.construct(o, e || [], _getPrototypeOf(t).constructor) : o.apply(t, e));
  }
  function _setPrototypeOf(t, e) {
    return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
      return t2.__proto__ = e2, t2;
    }, _setPrototypeOf(t, e);
  }
  function _inherits(t, e) {
    if ("function" != typeof e && null !== e) throw new TypeError("Super expression must either be null or a function");
    t.prototype = Object.create(e && e.prototype, {
      constructor: {
        value: t,
        writable: true,
        configurable: true
      }
    }), Object.defineProperty(t, "prototype", {
      writable: false
    }), e && _setPrototypeOf(t, e);
  }

  // node_modules/sigma/dist/colors-beb06eb2.esm.js
  function _arrayWithHoles(r) {
    if (Array.isArray(r)) return r;
  }
  function _iterableToArrayLimit(r, l) {
    var t = null == r ? null : "undefined" != typeof Symbol && r[Symbol.iterator] || r["@@iterator"];
    if (null != t) {
      var e, n, i, u, a = [], f = true, o = false;
      try {
        if (i = (t = t.call(r)).next, 0 === l) {
          if (Object(t) !== t) return;
          f = false;
        } else for (; !(f = (e = i.call(t)).done) && (a.push(e.value), a.length !== l); f = true) ;
      } catch (r2) {
        o = true, n = r2;
      } finally {
        try {
          if (!f && null != t.return && (u = t.return(), Object(u) !== u)) return;
        } finally {
          if (o) throw n;
        }
      }
      return a;
    }
  }
  function _arrayLikeToArray(r, a) {
    (null == a || a > r.length) && (a = r.length);
    for (var e = 0, n = Array(a); e < a; e++) n[e] = r[e];
    return n;
  }
  function _unsupportedIterableToArray(r, a) {
    if (r) {
      if ("string" == typeof r) return _arrayLikeToArray(r, a);
      var t = {}.toString.call(r).slice(8, -1);
      return "Object" === t && r.constructor && (t = r.constructor.name), "Map" === t || "Set" === t ? Array.from(r) : "Arguments" === t || /^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(t) ? _arrayLikeToArray(r, a) : void 0;
    }
  }
  function _nonIterableRest() {
    throw new TypeError("Invalid attempt to destructure non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method.");
  }
  function _slicedToArray(r, e) {
    return _arrayWithHoles(r) || _iterableToArrayLimit(r, e) || _unsupportedIterableToArray(r, e) || _nonIterableRest();
  }
  var HTML_COLORS = {
    black: "#000000",
    silver: "#C0C0C0",
    gray: "#808080",
    grey: "#808080",
    white: "#FFFFFF",
    maroon: "#800000",
    red: "#FF0000",
    purple: "#800080",
    fuchsia: "#FF00FF",
    green: "#008000",
    lime: "#00FF00",
    olive: "#808000",
    yellow: "#FFFF00",
    navy: "#000080",
    blue: "#0000FF",
    teal: "#008080",
    aqua: "#00FFFF",
    darkblue: "#00008B",
    mediumblue: "#0000CD",
    darkgreen: "#006400",
    darkcyan: "#008B8B",
    deepskyblue: "#00BFFF",
    darkturquoise: "#00CED1",
    mediumspringgreen: "#00FA9A",
    springgreen: "#00FF7F",
    cyan: "#00FFFF",
    midnightblue: "#191970",
    dodgerblue: "#1E90FF",
    lightseagreen: "#20B2AA",
    forestgreen: "#228B22",
    seagreen: "#2E8B57",
    darkslategray: "#2F4F4F",
    darkslategrey: "#2F4F4F",
    limegreen: "#32CD32",
    mediumseagreen: "#3CB371",
    turquoise: "#40E0D0",
    royalblue: "#4169E1",
    steelblue: "#4682B4",
    darkslateblue: "#483D8B",
    mediumturquoise: "#48D1CC",
    indigo: "#4B0082",
    darkolivegreen: "#556B2F",
    cadetblue: "#5F9EA0",
    cornflowerblue: "#6495ED",
    rebeccapurple: "#663399",
    mediumaquamarine: "#66CDAA",
    dimgray: "#696969",
    dimgrey: "#696969",
    slateblue: "#6A5ACD",
    olivedrab: "#6B8E23",
    slategray: "#708090",
    slategrey: "#708090",
    lightslategray: "#778899",
    lightslategrey: "#778899",
    mediumslateblue: "#7B68EE",
    lawngreen: "#7CFC00",
    chartreuse: "#7FFF00",
    aquamarine: "#7FFFD4",
    skyblue: "#87CEEB",
    lightskyblue: "#87CEFA",
    blueviolet: "#8A2BE2",
    darkred: "#8B0000",
    darkmagenta: "#8B008B",
    saddlebrown: "#8B4513",
    darkseagreen: "#8FBC8F",
    lightgreen: "#90EE90",
    mediumpurple: "#9370DB",
    darkviolet: "#9400D3",
    palegreen: "#98FB98",
    darkorchid: "#9932CC",
    yellowgreen: "#9ACD32",
    sienna: "#A0522D",
    brown: "#A52A2A",
    darkgray: "#A9A9A9",
    darkgrey: "#A9A9A9",
    lightblue: "#ADD8E6",
    greenyellow: "#ADFF2F",
    paleturquoise: "#AFEEEE",
    lightsteelblue: "#B0C4DE",
    powderblue: "#B0E0E6",
    firebrick: "#B22222",
    darkgoldenrod: "#B8860B",
    mediumorchid: "#BA55D3",
    rosybrown: "#BC8F8F",
    darkkhaki: "#BDB76B",
    mediumvioletred: "#C71585",
    indianred: "#CD5C5C",
    peru: "#CD853F",
    chocolate: "#D2691E",
    tan: "#D2B48C",
    lightgray: "#D3D3D3",
    lightgrey: "#D3D3D3",
    thistle: "#D8BFD8",
    orchid: "#DA70D6",
    goldenrod: "#DAA520",
    palevioletred: "#DB7093",
    crimson: "#DC143C",
    gainsboro: "#DCDCDC",
    plum: "#DDA0DD",
    burlywood: "#DEB887",
    lightcyan: "#E0FFFF",
    lavender: "#E6E6FA",
    darksalmon: "#E9967A",
    violet: "#EE82EE",
    palegoldenrod: "#EEE8AA",
    lightcoral: "#F08080",
    khaki: "#F0E68C",
    aliceblue: "#F0F8FF",
    honeydew: "#F0FFF0",
    azure: "#F0FFFF",
    sandybrown: "#F4A460",
    wheat: "#F5DEB3",
    beige: "#F5F5DC",
    whitesmoke: "#F5F5F5",
    mintcream: "#F5FFFA",
    ghostwhite: "#F8F8FF",
    salmon: "#FA8072",
    antiquewhite: "#FAEBD7",
    linen: "#FAF0E6",
    lightgoldenrodyellow: "#FAFAD2",
    oldlace: "#FDF5E6",
    magenta: "#FF00FF",
    deeppink: "#FF1493",
    orangered: "#FF4500",
    tomato: "#FF6347",
    hotpink: "#FF69B4",
    coral: "#FF7F50",
    darkorange: "#FF8C00",
    lightsalmon: "#FFA07A",
    orange: "#FFA500",
    lightpink: "#FFB6C1",
    pink: "#FFC0CB",
    gold: "#FFD700",
    peachpuff: "#FFDAB9",
    navajowhite: "#FFDEAD",
    moccasin: "#FFE4B5",
    bisque: "#FFE4C4",
    mistyrose: "#FFE4E1",
    blanchedalmond: "#FFEBCD",
    papayawhip: "#FFEFD5",
    lavenderblush: "#FFF0F5",
    seashell: "#FFF5EE",
    cornsilk: "#FFF8DC",
    lemonchiffon: "#FFFACD",
    floralwhite: "#FFFAF0",
    snow: "#FFFAFA",
    lightyellow: "#FFFFE0",
    ivory: "#FFFFF0"
  };
  var INT8 = new Int8Array(4);
  var INT32 = new Int32Array(INT8.buffer, 0, 1);
  var FLOAT32 = new Float32Array(INT8.buffer, 0, 1);
  var RGBA_TEST_REGEX = /^\s*rgba?\s*\(/;
  var RGBA_EXTRACT_REGEX = /^\s*rgba?\s*\(\s*([0-9]*)\s*,\s*([0-9]*)\s*,\s*([0-9]*)(?:\s*,\s*(.*)?)?\)\s*$/;
  function parseColor(val) {
    var r = 0;
    var g = 0;
    var b = 0;
    var a = 1;
    if (val[0] === "#") {
      if (val.length === 4) {
        r = parseInt(val.charAt(1) + val.charAt(1), 16);
        g = parseInt(val.charAt(2) + val.charAt(2), 16);
        b = parseInt(val.charAt(3) + val.charAt(3), 16);
      } else {
        r = parseInt(val.charAt(1) + val.charAt(2), 16);
        g = parseInt(val.charAt(3) + val.charAt(4), 16);
        b = parseInt(val.charAt(5) + val.charAt(6), 16);
      }
      if (val.length === 9) {
        a = parseInt(val.charAt(7) + val.charAt(8), 16) / 255;
      }
    } else if (RGBA_TEST_REGEX.test(val)) {
      var match = val.match(RGBA_EXTRACT_REGEX);
      if (match) {
        r = +match[1];
        g = +match[2];
        b = +match[3];
        if (match[4]) a = +match[4];
      }
    }
    return {
      r,
      g,
      b,
      a
    };
  }
  var FLOAT_COLOR_CACHE = {};
  for (htmlColor in HTML_COLORS) {
    FLOAT_COLOR_CACHE[htmlColor] = floatColor(HTML_COLORS[htmlColor]);
    FLOAT_COLOR_CACHE[HTML_COLORS[htmlColor]] = FLOAT_COLOR_CACHE[htmlColor];
  }
  var htmlColor;
  function rgbaToFloat(r, g, b, a, masking) {
    INT32[0] = a << 24 | b << 16 | g << 8 | r;
    if (masking) INT32[0] = INT32[0] & 4278190079;
    return FLOAT32[0];
  }
  function floatColor(val) {
    val = val.toLowerCase();
    if (typeof FLOAT_COLOR_CACHE[val] !== "undefined") return FLOAT_COLOR_CACHE[val];
    var parsed = parseColor(val);
    var r = parsed.r, g = parsed.g, b = parsed.b;
    var a = parsed.a;
    a = a * 255 | 0;
    var color = rgbaToFloat(r, g, b, a, true);
    FLOAT_COLOR_CACHE[val] = color;
    return color;
  }
  function colorToArray(val, masking) {
    FLOAT32[0] = floatColor(val);
    var intValue = INT32[0];
    if (masking) {
      intValue = intValue | 16777216;
    }
    var r = intValue & 255;
    var g = intValue >> 8 & 255;
    var b = intValue >> 16 & 255;
    var a = intValue >> 24 & 255;
    return [r, g, b, a];
  }
  var FLOAT_INDEX_CACHE = {};
  function indexToColor(index) {
    if (typeof FLOAT_INDEX_CACHE[index] !== "undefined") return FLOAT_INDEX_CACHE[index];
    var r = (index & 16711680) >>> 16;
    var g = (index & 65280) >>> 8;
    var b = index & 255;
    var a = 255;
    var color = rgbaToFloat(r, g, b, a, true);
    FLOAT_INDEX_CACHE[index] = color;
    return color;
  }
  function colorToIndex(r, g, b, _a) {
    return b + (g << 8) + (r << 16);
  }
  function getPixelColor(gl, frameBuffer, x, y, pixelRatio, downSizingRatio) {
    var bufferX = Math.floor(x / downSizingRatio * pixelRatio);
    var bufferY = Math.floor(gl.drawingBufferHeight / downSizingRatio - y / downSizingRatio * pixelRatio);
    var pixel = new Uint8Array(4);
    gl.bindFramebuffer(gl.FRAMEBUFFER, frameBuffer);
    gl.readPixels(bufferX, bufferY, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    var _pixel = _slicedToArray(pixel, 4), r = _pixel[0], g = _pixel[1], b = _pixel[2], a = _pixel[3];
    return [r, g, b, a];
  }

  // node_modules/sigma/dist/index-fad77a13.esm.js
  function _defineProperty(e, r, t) {
    return (r = _toPropertyKey(r)) in e ? Object.defineProperty(e, r, {
      value: t,
      enumerable: true,
      configurable: true,
      writable: true
    }) : e[r] = t, e;
  }
  function ownKeys(e, r) {
    var t = Object.keys(e);
    if (Object.getOwnPropertySymbols) {
      var o = Object.getOwnPropertySymbols(e);
      r && (o = o.filter(function(r2) {
        return Object.getOwnPropertyDescriptor(e, r2).enumerable;
      })), t.push.apply(t, o);
    }
    return t;
  }
  function _objectSpread2(e) {
    for (var r = 1; r < arguments.length; r++) {
      var t = null != arguments[r] ? arguments[r] : {};
      r % 2 ? ownKeys(Object(t), true).forEach(function(r2) {
        _defineProperty(e, r2, t[r2]);
      }) : Object.getOwnPropertyDescriptors ? Object.defineProperties(e, Object.getOwnPropertyDescriptors(t)) : ownKeys(Object(t)).forEach(function(r2) {
        Object.defineProperty(e, r2, Object.getOwnPropertyDescriptor(t, r2));
      });
    }
    return e;
  }
  function _superPropBase(t, o) {
    for (; !{}.hasOwnProperty.call(t, o) && null !== (t = _getPrototypeOf(t)); ) ;
    return t;
  }
  function _get() {
    return _get = "undefined" != typeof Reflect && Reflect.get ? Reflect.get.bind() : function(e, t, r) {
      var p = _superPropBase(e, t);
      if (p) {
        var n = Object.getOwnPropertyDescriptor(p, t);
        return n.get ? n.get.call(arguments.length < 3 ? e : r) : n.value;
      }
    }, _get.apply(null, arguments);
  }
  function _superPropGet(t, o, e, r) {
    var p = _get(_getPrototypeOf(1 & r ? t.prototype : t), o, e);
    return 2 & r && "function" == typeof p ? function(t2) {
      return p.apply(e, t2);
    } : p;
  }
  function getAttributeItemsCount(attr) {
    return attr.normalized ? 1 : attr.size;
  }
  function getAttributesItemsCount(attrs) {
    var res = 0;
    attrs.forEach(function(attr) {
      return res += getAttributeItemsCount(attr);
    });
    return res;
  }
  function loadShader(type, gl, source) {
    var glType = type === "VERTEX" ? gl.VERTEX_SHADER : gl.FRAGMENT_SHADER;
    var shader = gl.createShader(glType);
    if (shader === null) {
      throw new Error("loadShader: error while creating the shader");
    }
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    var successfullyCompiled = gl.getShaderParameter(shader, gl.COMPILE_STATUS);
    if (!successfullyCompiled) {
      var infoLog = gl.getShaderInfoLog(shader);
      gl.deleteShader(shader);
      throw new Error("loadShader: error while compiling the shader:\n".concat(infoLog, "\n").concat(source));
    }
    return shader;
  }
  function loadVertexShader(gl, source) {
    return loadShader("VERTEX", gl, source);
  }
  function loadFragmentShader(gl, source) {
    return loadShader("FRAGMENT", gl, source);
  }
  function loadProgram(gl, shaders) {
    var program = gl.createProgram();
    if (program === null) {
      throw new Error("loadProgram: error while creating the program.");
    }
    var i, l;
    for (i = 0, l = shaders.length; i < l; i++) gl.attachShader(program, shaders[i]);
    gl.linkProgram(program);
    var successfullyLinked = gl.getProgramParameter(program, gl.LINK_STATUS);
    if (!successfullyLinked) {
      var info = gl.getProgramInfoLog(program);
      gl.deleteProgram(program);
      throw new Error("loadProgram: error while linking the program: ".concat(info));
    }
    return program;
  }
  function killProgram(_ref) {
    var gl = _ref.gl, buffer = _ref.buffer, program = _ref.program, vertexShader = _ref.vertexShader, fragmentShader = _ref.fragmentShader;
    gl.deleteShader(vertexShader);
    gl.deleteShader(fragmentShader);
    gl.deleteProgram(program);
    gl.deleteBuffer(buffer);
  }
  function numberToGLSLFloat(n) {
    return n % 1 === 0 ? n.toFixed(1) : n.toString();
  }
  var PICKING_PREFIX = "#define PICKING_MODE\n";
  var SIZE_FACTOR_PER_ATTRIBUTE_TYPE = _defineProperty(_defineProperty(_defineProperty(_defineProperty(_defineProperty(_defineProperty(_defineProperty(_defineProperty({}, WebGL2RenderingContext.BOOL, 1), WebGL2RenderingContext.BYTE, 1), WebGL2RenderingContext.UNSIGNED_BYTE, 1), WebGL2RenderingContext.SHORT, 2), WebGL2RenderingContext.UNSIGNED_SHORT, 2), WebGL2RenderingContext.INT, 4), WebGL2RenderingContext.UNSIGNED_INT, 4), WebGL2RenderingContext.FLOAT, 4);
  var Program = /* @__PURE__ */ (function() {
    function Program2(gl, pickingBuffer, renderer) {
      _classCallCheck(this, Program2);
      _defineProperty(this, "array", new Float32Array());
      _defineProperty(this, "constantArray", new Float32Array());
      _defineProperty(this, "capacity", 0);
      _defineProperty(this, "verticesCount", 0);
      var def = this.getDefinition();
      this.VERTICES = def.VERTICES;
      this.VERTEX_SHADER_SOURCE = def.VERTEX_SHADER_SOURCE;
      this.FRAGMENT_SHADER_SOURCE = def.FRAGMENT_SHADER_SOURCE;
      this.UNIFORMS = def.UNIFORMS;
      this.ATTRIBUTES = def.ATTRIBUTES;
      this.METHOD = def.METHOD;
      this.CONSTANT_ATTRIBUTES = "CONSTANT_ATTRIBUTES" in def ? def.CONSTANT_ATTRIBUTES : [];
      this.CONSTANT_DATA = "CONSTANT_DATA" in def ? def.CONSTANT_DATA : [];
      this.isInstanced = "CONSTANT_ATTRIBUTES" in def;
      this.ATTRIBUTES_ITEMS_COUNT = getAttributesItemsCount(this.ATTRIBUTES);
      this.STRIDE = this.VERTICES * this.ATTRIBUTES_ITEMS_COUNT;
      this.renderer = renderer;
      this.normalProgram = this.getProgramInfo("normal", gl, def.VERTEX_SHADER_SOURCE, def.FRAGMENT_SHADER_SOURCE, null);
      this.pickProgram = pickingBuffer ? this.getProgramInfo("pick", gl, PICKING_PREFIX + def.VERTEX_SHADER_SOURCE, PICKING_PREFIX + def.FRAGMENT_SHADER_SOURCE, pickingBuffer) : null;
      if (this.isInstanced) {
        var constantAttributesItemsCount = getAttributesItemsCount(this.CONSTANT_ATTRIBUTES);
        if (this.CONSTANT_DATA.length !== this.VERTICES) throw new Error("Program: error while getting constant data (expected ".concat(this.VERTICES, " items, received ").concat(this.CONSTANT_DATA.length, " instead)"));
        this.constantArray = new Float32Array(this.CONSTANT_DATA.length * constantAttributesItemsCount);
        for (var i = 0; i < this.CONSTANT_DATA.length; i++) {
          var vector = this.CONSTANT_DATA[i];
          if (vector.length !== constantAttributesItemsCount) throw new Error("Program: error while getting constant data (one vector has ".concat(vector.length, " items instead of ").concat(constantAttributesItemsCount, ")"));
          for (var j = 0; j < vector.length; j++) this.constantArray[i * constantAttributesItemsCount + j] = vector[j];
        }
        this.STRIDE = this.ATTRIBUTES_ITEMS_COUNT;
      }
    }
    return _createClass(Program2, [{
      key: "kill",
      value: function kill() {
        killProgram(this.normalProgram);
        if (this.pickProgram) {
          killProgram(this.pickProgram);
          this.pickProgram = null;
        }
      }
    }, {
      key: "getProgramInfo",
      value: function getProgramInfo(name, gl, vertexShaderSource, fragmentShaderSource, frameBuffer) {
        var def = this.getDefinition();
        var buffer = gl.createBuffer();
        if (buffer === null) throw new Error("Program: error while creating the WebGL buffer.");
        var vertexShader = loadVertexShader(gl, vertexShaderSource);
        var fragmentShader = loadFragmentShader(gl, fragmentShaderSource);
        var program = loadProgram(gl, [vertexShader, fragmentShader]);
        var uniformLocations = {};
        def.UNIFORMS.forEach(function(uniformName) {
          var location = gl.getUniformLocation(program, uniformName);
          if (location) uniformLocations[uniformName] = location;
        });
        var attributeLocations = {};
        def.ATTRIBUTES.forEach(function(attr) {
          attributeLocations[attr.name] = gl.getAttribLocation(program, attr.name);
        });
        var constantBuffer;
        if ("CONSTANT_ATTRIBUTES" in def) {
          def.CONSTANT_ATTRIBUTES.forEach(function(attr) {
            attributeLocations[attr.name] = gl.getAttribLocation(program, attr.name);
          });
          constantBuffer = gl.createBuffer();
          if (constantBuffer === null) throw new Error("Program: error while creating the WebGL constant buffer.");
        }
        return {
          name,
          program,
          gl,
          frameBuffer,
          buffer,
          constantBuffer: constantBuffer || {},
          uniformLocations,
          attributeLocations,
          isPicking: name === "pick",
          vertexShader,
          fragmentShader
        };
      }
    }, {
      key: "bindProgram",
      value: function bindProgram(program) {
        var _this = this;
        var offset = 0;
        var gl = program.gl, buffer = program.buffer;
        if (!this.isInstanced) {
          gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
          offset = 0;
          this.ATTRIBUTES.forEach(function(attr) {
            return offset += _this.bindAttribute(attr, program, offset);
          });
          gl.bufferData(gl.ARRAY_BUFFER, this.array, gl.DYNAMIC_DRAW);
        } else {
          gl.bindBuffer(gl.ARRAY_BUFFER, program.constantBuffer);
          offset = 0;
          this.CONSTANT_ATTRIBUTES.forEach(function(attr) {
            return offset += _this.bindAttribute(attr, program, offset, false);
          });
          gl.bufferData(gl.ARRAY_BUFFER, this.constantArray, gl.STATIC_DRAW);
          gl.bindBuffer(gl.ARRAY_BUFFER, program.buffer);
          offset = 0;
          this.ATTRIBUTES.forEach(function(attr) {
            return offset += _this.bindAttribute(attr, program, offset, true);
          });
          gl.bufferData(gl.ARRAY_BUFFER, this.array, gl.DYNAMIC_DRAW);
        }
        gl.bindBuffer(gl.ARRAY_BUFFER, null);
      }
    }, {
      key: "unbindProgram",
      value: function unbindProgram(program) {
        var _this2 = this;
        if (!this.isInstanced) {
          this.ATTRIBUTES.forEach(function(attr) {
            return _this2.unbindAttribute(attr, program);
          });
        } else {
          this.CONSTANT_ATTRIBUTES.forEach(function(attr) {
            return _this2.unbindAttribute(attr, program, false);
          });
          this.ATTRIBUTES.forEach(function(attr) {
            return _this2.unbindAttribute(attr, program, true);
          });
        }
      }
    }, {
      key: "bindAttribute",
      value: function bindAttribute(attr, program, offset, setDivisor) {
        var sizeFactor = SIZE_FACTOR_PER_ATTRIBUTE_TYPE[attr.type];
        if (typeof sizeFactor !== "number") throw new Error('Program.bind: yet unsupported attribute type "'.concat(attr.type, '"'));
        var location = program.attributeLocations[attr.name];
        var gl = program.gl;
        if (location !== -1) {
          gl.enableVertexAttribArray(location);
          var stride = !this.isInstanced ? this.ATTRIBUTES_ITEMS_COUNT * Float32Array.BYTES_PER_ELEMENT : (setDivisor ? this.ATTRIBUTES_ITEMS_COUNT : getAttributesItemsCount(this.CONSTANT_ATTRIBUTES)) * Float32Array.BYTES_PER_ELEMENT;
          gl.vertexAttribPointer(location, attr.size, attr.type, attr.normalized || false, stride, offset);
          if (this.isInstanced && setDivisor) {
            if (gl instanceof WebGL2RenderingContext) {
              gl.vertexAttribDivisor(location, 1);
            } else {
              var ext = gl.getExtension("ANGLE_instanced_arrays");
              if (ext) ext.vertexAttribDivisorANGLE(location, 1);
            }
          }
        }
        return attr.size * sizeFactor;
      }
    }, {
      key: "unbindAttribute",
      value: function unbindAttribute(attr, program, unsetDivisor) {
        var location = program.attributeLocations[attr.name];
        var gl = program.gl;
        if (location !== -1) {
          gl.disableVertexAttribArray(location);
          if (this.isInstanced && unsetDivisor) {
            if (gl instanceof WebGL2RenderingContext) {
              gl.vertexAttribDivisor(location, 0);
            } else {
              var ext = gl.getExtension("ANGLE_instanced_arrays");
              if (ext) ext.vertexAttribDivisorANGLE(location, 0);
            }
          }
        }
      }
    }, {
      key: "reallocate",
      value: function reallocate(capacity) {
        if (capacity === this.capacity) return;
        this.capacity = capacity;
        this.verticesCount = this.VERTICES * capacity;
        this.array = new Float32Array(!this.isInstanced ? this.verticesCount * this.ATTRIBUTES_ITEMS_COUNT : this.capacity * this.ATTRIBUTES_ITEMS_COUNT);
      }
    }, {
      key: "hasNothingToRender",
      value: function hasNothingToRender() {
        return this.verticesCount === 0;
      }
    }, {
      key: "renderProgram",
      value: function renderProgram(params, programInfo) {
        var gl = programInfo.gl, program = programInfo.program;
        gl.enable(gl.BLEND);
        gl.useProgram(program);
        this.setUniforms(params, programInfo);
        this.drawWebGL(this.METHOD, programInfo);
      }
    }, {
      key: "render",
      value: function render(params) {
        if (this.hasNothingToRender()) return;
        if (this.pickProgram) {
          this.pickProgram.gl.viewport(0, 0, params.width * params.pixelRatio / params.downSizingRatio, params.height * params.pixelRatio / params.downSizingRatio);
          this.bindProgram(this.pickProgram);
          this.renderProgram(_objectSpread2(_objectSpread2({}, params), {}, {
            pixelRatio: params.pixelRatio / params.downSizingRatio
          }), this.pickProgram);
          this.unbindProgram(this.pickProgram);
        }
        this.normalProgram.gl.viewport(0, 0, params.width * params.pixelRatio, params.height * params.pixelRatio);
        this.bindProgram(this.normalProgram);
        this.renderProgram(params, this.normalProgram);
        this.unbindProgram(this.normalProgram);
      }
    }, {
      key: "drawWebGL",
      value: function drawWebGL(method, _ref) {
        var gl = _ref.gl, frameBuffer = _ref.frameBuffer;
        gl.bindFramebuffer(gl.FRAMEBUFFER, frameBuffer);
        if (!this.isInstanced) {
          gl.drawArrays(method, 0, this.verticesCount);
        } else {
          if (gl instanceof WebGL2RenderingContext) {
            gl.drawArraysInstanced(method, 0, this.VERTICES, this.capacity);
          } else {
            var ext = gl.getExtension("ANGLE_instanced_arrays");
            if (ext) ext.drawArraysInstancedANGLE(method, 0, this.VERTICES, this.capacity);
          }
        }
      }
    }]);
  })();
  var NodeProgram = /* @__PURE__ */ (function(_ref) {
    function NodeProgram2() {
      _classCallCheck(this, NodeProgram2);
      return _callSuper(this, NodeProgram2, arguments);
    }
    _inherits(NodeProgram2, _ref);
    return _createClass(NodeProgram2, [{
      key: "kill",
      value: function kill() {
        _superPropGet(NodeProgram2, "kill", this, 3)([]);
      }
    }, {
      key: "process",
      value: function process(nodeIndex, offset, data) {
        var i = offset * this.STRIDE;
        if (data.hidden) {
          for (var l = i + this.STRIDE; i < l; i++) {
            this.array[i] = 0;
          }
          return;
        }
        return this.processVisibleItem(indexToColor(nodeIndex), i, data);
      }
    }]);
  })(Program);
  var EdgeProgram = /* @__PURE__ */ (function(_ref) {
    function EdgeProgram2() {
      var _this;
      _classCallCheck(this, EdgeProgram2);
      for (var _len = arguments.length, args = new Array(_len), _key = 0; _key < _len; _key++) {
        args[_key] = arguments[_key];
      }
      _this = _callSuper(this, EdgeProgram2, [].concat(args));
      _defineProperty(_this, "drawLabel", void 0);
      return _this;
    }
    _inherits(EdgeProgram2, _ref);
    return _createClass(EdgeProgram2, [{
      key: "kill",
      value: function kill() {
        _superPropGet(EdgeProgram2, "kill", this, 3)([]);
      }
    }, {
      key: "process",
      value: function process(edgeIndex, offset, sourceData, targetData, data) {
        var i = offset * this.STRIDE;
        if (data.hidden || sourceData.hidden || targetData.hidden) {
          for (var l = i + this.STRIDE; i < l; i++) {
            this.array[i] = 0;
          }
          return;
        }
        return this.processVisibleItem(indexToColor(edgeIndex), i, sourceData, targetData, data);
      }
    }]);
  })(Program);
  function createEdgeCompoundProgram(programClasses, drawLabel) {
    return /* @__PURE__ */ (function() {
      function EdgeCompoundProgram(gl, pickingBuffer, renderer) {
        _classCallCheck(this, EdgeCompoundProgram);
        _defineProperty(this, "drawLabel", drawLabel);
        this.programs = programClasses.map(function(Program2) {
          return new Program2(gl, pickingBuffer, renderer);
        });
      }
      return _createClass(EdgeCompoundProgram, [{
        key: "reallocate",
        value: function reallocate(capacity) {
          this.programs.forEach(function(program) {
            return program.reallocate(capacity);
          });
        }
      }, {
        key: "process",
        value: function process(edgeIndex, offset, sourceData, targetData, data) {
          this.programs.forEach(function(program) {
            return program.process(edgeIndex, offset, sourceData, targetData, data);
          });
        }
      }, {
        key: "render",
        value: function render(params) {
          this.programs.forEach(function(program) {
            return program.render(params);
          });
        }
      }, {
        key: "kill",
        value: function kill() {
          this.programs.forEach(function(program) {
            return program.kill();
          });
        }
      }]);
    })();
  }
  function drawStraightEdgeLabel(context, edgeData, sourceData, targetData, settings) {
    var size = settings.edgeLabelSize, font = settings.edgeLabelFont, weight = settings.edgeLabelWeight, color = settings.edgeLabelColor.attribute ? edgeData[settings.edgeLabelColor.attribute] || settings.edgeLabelColor.color || "#000" : settings.edgeLabelColor.color;
    var label = edgeData.label;
    if (!label) return;
    context.fillStyle = color;
    context.font = "".concat(weight, " ").concat(size, "px ").concat(font);
    var sSize = sourceData.size;
    var tSize = targetData.size;
    var sx = sourceData.x;
    var sy = sourceData.y;
    var tx = targetData.x;
    var ty = targetData.y;
    var cx = (sx + tx) / 2;
    var cy = (sy + ty) / 2;
    var dx = tx - sx;
    var dy = ty - sy;
    var d = Math.sqrt(dx * dx + dy * dy);
    if (d < sSize + tSize) return;
    sx += dx * sSize / d;
    sy += dy * sSize / d;
    tx -= dx * tSize / d;
    ty -= dy * tSize / d;
    cx = (sx + tx) / 2;
    cy = (sy + ty) / 2;
    dx = tx - sx;
    dy = ty - sy;
    d = Math.sqrt(dx * dx + dy * dy);
    var textLength = context.measureText(label).width;
    if (textLength > d) {
      var ellipsis = "\u2026";
      label = label + ellipsis;
      textLength = context.measureText(label).width;
      while (textLength > d && label.length > 1) {
        label = label.slice(0, -2) + ellipsis;
        textLength = context.measureText(label).width;
      }
      if (label.length < 4) return;
    }
    var angle;
    if (dx > 0) {
      if (dy > 0) angle = Math.acos(dx / d);
      else angle = Math.asin(dy / d);
    } else {
      if (dy > 0) angle = Math.acos(dx / d) + Math.PI;
      else angle = Math.asin(dx / d) + Math.PI / 2;
    }
    context.save();
    context.translate(cx, cy);
    context.rotate(angle);
    context.fillText(label, -textLength / 2, edgeData.size / 2 + size);
    context.restore();
  }
  function drawDiscNodeLabel(context, data, settings) {
    if (!data.label) return;
    var size = settings.labelSize, font = settings.labelFont, weight = settings.labelWeight, color = settings.labelColor.attribute ? data[settings.labelColor.attribute] || settings.labelColor.color || "#000" : settings.labelColor.color;
    context.fillStyle = color;
    context.font = "".concat(weight, " ").concat(size, "px ").concat(font);
    context.fillText(data.label, data.x + data.size + 3, data.y + size / 3);
  }
  function drawDiscNodeHover(context, data, settings) {
    var size = settings.labelSize, font = settings.labelFont, weight = settings.labelWeight;
    context.font = "".concat(weight, " ").concat(size, "px ").concat(font);
    context.fillStyle = "#FFF";
    context.shadowOffsetX = 0;
    context.shadowOffsetY = 0;
    context.shadowBlur = 8;
    context.shadowColor = "#000";
    var PADDING = 2;
    if (typeof data.label === "string") {
      var textWidth = context.measureText(data.label).width, boxWidth = Math.round(textWidth + 5), boxHeight = Math.round(size + 2 * PADDING), radius = Math.max(data.size, size / 2) + PADDING;
      var angleRadian = Math.asin(boxHeight / 2 / radius);
      var xDeltaCoord = Math.sqrt(Math.abs(Math.pow(radius, 2) - Math.pow(boxHeight / 2, 2)));
      context.beginPath();
      context.moveTo(data.x + xDeltaCoord, data.y + boxHeight / 2);
      context.lineTo(data.x + radius + boxWidth, data.y + boxHeight / 2);
      context.lineTo(data.x + radius + boxWidth, data.y - boxHeight / 2);
      context.lineTo(data.x + xDeltaCoord, data.y - boxHeight / 2);
      context.arc(data.x, data.y, radius, angleRadian, -angleRadian);
      context.closePath();
      context.fill();
    } else {
      context.beginPath();
      context.arc(data.x, data.y, data.size + PADDING, 0, Math.PI * 2);
      context.closePath();
      context.fill();
    }
    context.shadowOffsetX = 0;
    context.shadowOffsetY = 0;
    context.shadowBlur = 0;
    drawDiscNodeLabel(context, data, settings);
  }
  var SHADER_SOURCE$6 = (
    /*glsl*/
    "\nprecision highp float;\n\nvarying vec4 v_color;\nvarying vec2 v_diffVector;\nvarying float v_radius;\n\nuniform float u_correctionRatio;\n\nconst vec4 transparent = vec4(0.0, 0.0, 0.0, 0.0);\n\nvoid main(void) {\n  float border = u_correctionRatio * 2.0;\n  float dist = length(v_diffVector) - v_radius + border;\n\n  // No antialiasing for picking mode:\n  #ifdef PICKING_MODE\n  if (dist > border)\n    gl_FragColor = transparent;\n  else\n    gl_FragColor = v_color;\n\n  #else\n  float t = 0.0;\n  if (dist > border)\n    t = 1.0;\n  else if (dist > 0.0)\n    t = dist / border;\n\n  gl_FragColor = mix(v_color, transparent, t);\n  #endif\n}\n"
  );
  var FRAGMENT_SHADER_SOURCE$2 = SHADER_SOURCE$6;
  var SHADER_SOURCE$5 = (
    /*glsl*/
    "\nattribute vec4 a_id;\nattribute vec4 a_color;\nattribute vec2 a_position;\nattribute float a_size;\nattribute float a_angle;\n\nuniform mat3 u_matrix;\nuniform float u_sizeRatio;\nuniform float u_correctionRatio;\n\nvarying vec4 v_color;\nvarying vec2 v_diffVector;\nvarying float v_radius;\nvarying float v_border;\n\nconst float bias = 255.0 / 254.0;\n\nvoid main() {\n  float size = a_size * u_correctionRatio / u_sizeRatio * 4.0;\n  vec2 diffVector = size * vec2(cos(a_angle), sin(a_angle));\n  vec2 position = a_position + diffVector;\n  gl_Position = vec4(\n    (u_matrix * vec3(position, 1)).xy,\n    0,\n    1\n  );\n\n  v_diffVector = diffVector;\n  v_radius = size / 2.0;\n\n  #ifdef PICKING_MODE\n  // For picking mode, we use the ID as the color:\n  v_color = a_id;\n  #else\n  // For normal mode, we use the color:\n  v_color = a_color;\n  #endif\n\n  v_color.a *= bias;\n}\n"
  );
  var VERTEX_SHADER_SOURCE$3 = SHADER_SOURCE$5;
  var _WebGLRenderingContex$3 = WebGLRenderingContext;
  var UNSIGNED_BYTE$3 = _WebGLRenderingContex$3.UNSIGNED_BYTE;
  var FLOAT$3 = _WebGLRenderingContex$3.FLOAT;
  var UNIFORMS$3 = ["u_sizeRatio", "u_correctionRatio", "u_matrix"];
  var NodeCircleProgram = /* @__PURE__ */ (function(_NodeProgram) {
    function NodeCircleProgram2() {
      _classCallCheck(this, NodeCircleProgram2);
      return _callSuper(this, NodeCircleProgram2, arguments);
    }
    _inherits(NodeCircleProgram2, _NodeProgram);
    return _createClass(NodeCircleProgram2, [{
      key: "getDefinition",
      value: function getDefinition() {
        return {
          VERTICES: 3,
          VERTEX_SHADER_SOURCE: VERTEX_SHADER_SOURCE$3,
          FRAGMENT_SHADER_SOURCE: FRAGMENT_SHADER_SOURCE$2,
          METHOD: WebGLRenderingContext.TRIANGLES,
          UNIFORMS: UNIFORMS$3,
          ATTRIBUTES: [{
            name: "a_position",
            size: 2,
            type: FLOAT$3
          }, {
            name: "a_size",
            size: 1,
            type: FLOAT$3
          }, {
            name: "a_color",
            size: 4,
            type: UNSIGNED_BYTE$3,
            normalized: true
          }, {
            name: "a_id",
            size: 4,
            type: UNSIGNED_BYTE$3,
            normalized: true
          }],
          CONSTANT_ATTRIBUTES: [{
            name: "a_angle",
            size: 1,
            type: FLOAT$3
          }],
          CONSTANT_DATA: [[NodeCircleProgram2.ANGLE_1], [NodeCircleProgram2.ANGLE_2], [NodeCircleProgram2.ANGLE_3]]
        };
      }
    }, {
      key: "processVisibleItem",
      value: function processVisibleItem(nodeIndex, startIndex, data) {
        var array = this.array;
        var color = floatColor(data.color);
        array[startIndex++] = data.x;
        array[startIndex++] = data.y;
        array[startIndex++] = data.size;
        array[startIndex++] = color;
        array[startIndex++] = nodeIndex;
      }
    }, {
      key: "setUniforms",
      value: function setUniforms(params, _ref) {
        var gl = _ref.gl, uniformLocations = _ref.uniformLocations;
        var u_sizeRatio = uniformLocations.u_sizeRatio, u_correctionRatio = uniformLocations.u_correctionRatio, u_matrix = uniformLocations.u_matrix;
        gl.uniform1f(u_correctionRatio, params.correctionRatio);
        gl.uniform1f(u_sizeRatio, params.sizeRatio);
        gl.uniformMatrix3fv(u_matrix, false, params.matrix);
      }
    }]);
  })(NodeProgram);
  _defineProperty(NodeCircleProgram, "ANGLE_1", 0);
  _defineProperty(NodeCircleProgram, "ANGLE_2", 2 * Math.PI / 3);
  _defineProperty(NodeCircleProgram, "ANGLE_3", 4 * Math.PI / 3);
  var SHADER_SOURCE$4 = (
    /*glsl*/
    "\nprecision mediump float;\n\nvarying vec4 v_color;\n\nvoid main(void) {\n  gl_FragColor = v_color;\n}\n"
  );
  var FRAGMENT_SHADER_SOURCE$1 = SHADER_SOURCE$4;
  var SHADER_SOURCE$3 = (
    /*glsl*/
    "\nattribute vec2 a_position;\nattribute vec2 a_normal;\nattribute float a_radius;\nattribute vec3 a_barycentric;\n\n#ifdef PICKING_MODE\nattribute vec4 a_id;\n#else\nattribute vec4 a_color;\n#endif\n\nuniform mat3 u_matrix;\nuniform float u_sizeRatio;\nuniform float u_correctionRatio;\nuniform float u_minEdgeThickness;\nuniform float u_lengthToThicknessRatio;\nuniform float u_widenessToThicknessRatio;\n\nvarying vec4 v_color;\n\nconst float bias = 255.0 / 254.0;\n\nvoid main() {\n  float minThickness = u_minEdgeThickness;\n\n  float normalLength = length(a_normal);\n  vec2 unitNormal = a_normal / normalLength;\n\n  // These first computations are taken from edge.vert.glsl and\n  // edge.clamped.vert.glsl. Please read it to get better comments on what's\n  // happening:\n  float pixelsThickness = max(normalLength / u_sizeRatio, minThickness);\n  float webGLThickness = pixelsThickness * u_correctionRatio;\n  float webGLNodeRadius = a_radius * 2.0 * u_correctionRatio / u_sizeRatio;\n  float webGLArrowHeadLength = webGLThickness * u_lengthToThicknessRatio * 2.0;\n  float webGLArrowHeadThickness = webGLThickness * u_widenessToThicknessRatio;\n\n  float da = a_barycentric.x;\n  float db = a_barycentric.y;\n  float dc = a_barycentric.z;\n\n  vec2 delta = vec2(\n      da * (webGLNodeRadius * unitNormal.y)\n    + db * ((webGLNodeRadius + webGLArrowHeadLength) * unitNormal.y + webGLArrowHeadThickness * unitNormal.x)\n    + dc * ((webGLNodeRadius + webGLArrowHeadLength) * unitNormal.y - webGLArrowHeadThickness * unitNormal.x),\n\n      da * (-webGLNodeRadius * unitNormal.x)\n    + db * (-(webGLNodeRadius + webGLArrowHeadLength) * unitNormal.x + webGLArrowHeadThickness * unitNormal.y)\n    + dc * (-(webGLNodeRadius + webGLArrowHeadLength) * unitNormal.x - webGLArrowHeadThickness * unitNormal.y)\n  );\n\n  vec2 position = (u_matrix * vec3(a_position + delta, 1)).xy;\n\n  gl_Position = vec4(position, 0, 1);\n\n  #ifdef PICKING_MODE\n  // For picking mode, we use the ID as the color:\n  v_color = a_id;\n  #else\n  // For normal mode, we use the color:\n  v_color = a_color;\n  #endif\n\n  v_color.a *= bias;\n}\n"
  );
  var VERTEX_SHADER_SOURCE$2 = SHADER_SOURCE$3;
  var _WebGLRenderingContex$2 = WebGLRenderingContext;
  var UNSIGNED_BYTE$2 = _WebGLRenderingContex$2.UNSIGNED_BYTE;
  var FLOAT$2 = _WebGLRenderingContex$2.FLOAT;
  var UNIFORMS$2 = ["u_matrix", "u_sizeRatio", "u_correctionRatio", "u_minEdgeThickness", "u_lengthToThicknessRatio", "u_widenessToThicknessRatio"];
  var DEFAULT_EDGE_ARROW_HEAD_PROGRAM_OPTIONS = {
    extremity: "target",
    lengthToThicknessRatio: 2.5,
    widenessToThicknessRatio: 2
  };
  function createEdgeArrowHeadProgram(inputOptions) {
    var options = _objectSpread2(_objectSpread2({}, DEFAULT_EDGE_ARROW_HEAD_PROGRAM_OPTIONS), inputOptions || {});
    return /* @__PURE__ */ (function(_EdgeProgram) {
      function EdgeArrowHeadProgram2() {
        _classCallCheck(this, EdgeArrowHeadProgram2);
        return _callSuper(this, EdgeArrowHeadProgram2, arguments);
      }
      _inherits(EdgeArrowHeadProgram2, _EdgeProgram);
      return _createClass(EdgeArrowHeadProgram2, [{
        key: "getDefinition",
        value: function getDefinition() {
          return {
            VERTICES: 3,
            VERTEX_SHADER_SOURCE: VERTEX_SHADER_SOURCE$2,
            FRAGMENT_SHADER_SOURCE: FRAGMENT_SHADER_SOURCE$1,
            METHOD: WebGLRenderingContext.TRIANGLES,
            UNIFORMS: UNIFORMS$2,
            ATTRIBUTES: [{
              name: "a_position",
              size: 2,
              type: FLOAT$2
            }, {
              name: "a_normal",
              size: 2,
              type: FLOAT$2
            }, {
              name: "a_radius",
              size: 1,
              type: FLOAT$2
            }, {
              name: "a_color",
              size: 4,
              type: UNSIGNED_BYTE$2,
              normalized: true
            }, {
              name: "a_id",
              size: 4,
              type: UNSIGNED_BYTE$2,
              normalized: true
            }],
            CONSTANT_ATTRIBUTES: [{
              name: "a_barycentric",
              size: 3,
              type: FLOAT$2
            }],
            CONSTANT_DATA: [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
          };
        }
      }, {
        key: "processVisibleItem",
        value: function processVisibleItem(edgeIndex, startIndex, sourceData, targetData, data) {
          if (options.extremity === "source") {
            var _ref = [targetData, sourceData];
            sourceData = _ref[0];
            targetData = _ref[1];
          }
          var thickness = data.size || 1;
          var radius = targetData.size || 1;
          var x1 = sourceData.x;
          var y1 = sourceData.y;
          var x2 = targetData.x;
          var y2 = targetData.y;
          var color = floatColor(data.color);
          var dx = x2 - x1;
          var dy = y2 - y1;
          var len = dx * dx + dy * dy;
          var n1 = 0;
          var n2 = 0;
          if (len) {
            len = 1 / Math.sqrt(len);
            n1 = -dy * len * thickness;
            n2 = dx * len * thickness;
          }
          var array = this.array;
          array[startIndex++] = x2;
          array[startIndex++] = y2;
          array[startIndex++] = -n1;
          array[startIndex++] = -n2;
          array[startIndex++] = radius;
          array[startIndex++] = color;
          array[startIndex++] = edgeIndex;
        }
      }, {
        key: "setUniforms",
        value: function setUniforms(params, _ref2) {
          var gl = _ref2.gl, uniformLocations = _ref2.uniformLocations;
          var u_matrix = uniformLocations.u_matrix, u_sizeRatio = uniformLocations.u_sizeRatio, u_correctionRatio = uniformLocations.u_correctionRatio, u_minEdgeThickness = uniformLocations.u_minEdgeThickness, u_lengthToThicknessRatio = uniformLocations.u_lengthToThicknessRatio, u_widenessToThicknessRatio = uniformLocations.u_widenessToThicknessRatio;
          gl.uniformMatrix3fv(u_matrix, false, params.matrix);
          gl.uniform1f(u_sizeRatio, params.sizeRatio);
          gl.uniform1f(u_correctionRatio, params.correctionRatio);
          gl.uniform1f(u_minEdgeThickness, params.minEdgeThickness);
          gl.uniform1f(u_lengthToThicknessRatio, options.lengthToThicknessRatio);
          gl.uniform1f(u_widenessToThicknessRatio, options.widenessToThicknessRatio);
        }
      }]);
    })(EdgeProgram);
  }
  var EdgeArrowHeadProgram = createEdgeArrowHeadProgram();
  var SHADER_SOURCE$2 = (
    /*glsl*/
    "\nprecision mediump float;\n\nvarying vec4 v_color;\nvarying vec2 v_normal;\nvarying float v_thickness;\nvarying float v_feather;\n\nconst vec4 transparent = vec4(0.0, 0.0, 0.0, 0.0);\n\nvoid main(void) {\n  // We only handle antialiasing for normal mode:\n  #ifdef PICKING_MODE\n  gl_FragColor = v_color;\n  #else\n  float dist = length(v_normal) * v_thickness;\n\n  float t = smoothstep(\n    v_thickness - v_feather,\n    v_thickness,\n    dist\n  );\n\n  gl_FragColor = mix(v_color, transparent, t);\n  #endif\n}\n"
  );
  var FRAGMENT_SHADER_SOURCE = SHADER_SOURCE$2;
  var SHADER_SOURCE$1 = (
    /*glsl*/
    "\nattribute vec4 a_id;\nattribute vec4 a_color;\nattribute vec2 a_normal;\nattribute float a_normalCoef;\nattribute vec2 a_positionStart;\nattribute vec2 a_positionEnd;\nattribute float a_positionCoef;\nattribute float a_radius;\nattribute float a_radiusCoef;\n\nuniform mat3 u_matrix;\nuniform float u_zoomRatio;\nuniform float u_sizeRatio;\nuniform float u_pixelRatio;\nuniform float u_correctionRatio;\nuniform float u_minEdgeThickness;\nuniform float u_lengthToThicknessRatio;\nuniform float u_feather;\n\nvarying vec4 v_color;\nvarying vec2 v_normal;\nvarying float v_thickness;\nvarying float v_feather;\n\nconst float bias = 255.0 / 254.0;\n\nvoid main() {\n  float minThickness = u_minEdgeThickness;\n\n  float radius = a_radius * a_radiusCoef;\n  vec2 normal = a_normal * a_normalCoef;\n  vec2 position = a_positionStart * (1.0 - a_positionCoef) + a_positionEnd * a_positionCoef;\n\n  float normalLength = length(normal);\n  vec2 unitNormal = normal / normalLength;\n\n  // These first computations are taken from edge.vert.glsl. Please read it to\n  // get better comments on what's happening:\n  float pixelsThickness = max(normalLength, minThickness * u_sizeRatio);\n  float webGLThickness = pixelsThickness * u_correctionRatio / u_sizeRatio;\n\n  // Here, we move the point to leave space for the arrow head:\n  float direction = sign(radius);\n  float webGLNodeRadius = direction * radius * 2.0 * u_correctionRatio / u_sizeRatio;\n  float webGLArrowHeadLength = webGLThickness * u_lengthToThicknessRatio * 2.0;\n\n  vec2 compensationVector = vec2(-direction * unitNormal.y, direction * unitNormal.x) * (webGLNodeRadius + webGLArrowHeadLength);\n\n  // Here is the proper position of the vertex\n  gl_Position = vec4((u_matrix * vec3(position + unitNormal * webGLThickness + compensationVector, 1)).xy, 0, 1);\n\n  v_thickness = webGLThickness / u_zoomRatio;\n\n  v_normal = unitNormal;\n\n  v_feather = u_feather * u_correctionRatio / u_zoomRatio / u_pixelRatio * 2.0;\n\n  #ifdef PICKING_MODE\n  // For picking mode, we use the ID as the color:\n  v_color = a_id;\n  #else\n  // For normal mode, we use the color:\n  v_color = a_color;\n  #endif\n\n  v_color.a *= bias;\n}\n"
  );
  var VERTEX_SHADER_SOURCE$1 = SHADER_SOURCE$1;
  var _WebGLRenderingContex$1 = WebGLRenderingContext;
  var UNSIGNED_BYTE$1 = _WebGLRenderingContex$1.UNSIGNED_BYTE;
  var FLOAT$1 = _WebGLRenderingContex$1.FLOAT;
  var UNIFORMS$1 = ["u_matrix", "u_zoomRatio", "u_sizeRatio", "u_correctionRatio", "u_pixelRatio", "u_feather", "u_minEdgeThickness", "u_lengthToThicknessRatio"];
  var DEFAULT_EDGE_CLAMPED_PROGRAM_OPTIONS = {
    lengthToThicknessRatio: DEFAULT_EDGE_ARROW_HEAD_PROGRAM_OPTIONS.lengthToThicknessRatio
  };
  function createEdgeClampedProgram(inputOptions) {
    var options = _objectSpread2(_objectSpread2({}, DEFAULT_EDGE_CLAMPED_PROGRAM_OPTIONS), inputOptions || {});
    return /* @__PURE__ */ (function(_EdgeProgram) {
      function EdgeClampedProgram2() {
        _classCallCheck(this, EdgeClampedProgram2);
        return _callSuper(this, EdgeClampedProgram2, arguments);
      }
      _inherits(EdgeClampedProgram2, _EdgeProgram);
      return _createClass(EdgeClampedProgram2, [{
        key: "getDefinition",
        value: function getDefinition() {
          return {
            VERTICES: 6,
            VERTEX_SHADER_SOURCE: VERTEX_SHADER_SOURCE$1,
            FRAGMENT_SHADER_SOURCE,
            METHOD: WebGLRenderingContext.TRIANGLES,
            UNIFORMS: UNIFORMS$1,
            ATTRIBUTES: [{
              name: "a_positionStart",
              size: 2,
              type: FLOAT$1
            }, {
              name: "a_positionEnd",
              size: 2,
              type: FLOAT$1
            }, {
              name: "a_normal",
              size: 2,
              type: FLOAT$1
            }, {
              name: "a_color",
              size: 4,
              type: UNSIGNED_BYTE$1,
              normalized: true
            }, {
              name: "a_id",
              size: 4,
              type: UNSIGNED_BYTE$1,
              normalized: true
            }, {
              name: "a_radius",
              size: 1,
              type: FLOAT$1
            }],
            CONSTANT_ATTRIBUTES: [
              // If 0, then position will be a_positionStart
              // If 1, then position will be a_positionEnd
              {
                name: "a_positionCoef",
                size: 1,
                type: FLOAT$1
              },
              {
                name: "a_normalCoef",
                size: 1,
                type: FLOAT$1
              },
              {
                name: "a_radiusCoef",
                size: 1,
                type: FLOAT$1
              }
            ],
            CONSTANT_DATA: [[0, 1, 0], [0, -1, 0], [1, 1, 1], [1, 1, 1], [0, -1, 0], [1, -1, -1]]
          };
        }
      }, {
        key: "processVisibleItem",
        value: function processVisibleItem(edgeIndex, startIndex, sourceData, targetData, data) {
          var thickness = data.size || 1;
          var x1 = sourceData.x;
          var y1 = sourceData.y;
          var x2 = targetData.x;
          var y2 = targetData.y;
          var color = floatColor(data.color);
          var dx = x2 - x1;
          var dy = y2 - y1;
          var radius = targetData.size || 1;
          var len = dx * dx + dy * dy;
          var n1 = 0;
          var n2 = 0;
          if (len) {
            len = 1 / Math.sqrt(len);
            n1 = -dy * len * thickness;
            n2 = dx * len * thickness;
          }
          var array = this.array;
          array[startIndex++] = x1;
          array[startIndex++] = y1;
          array[startIndex++] = x2;
          array[startIndex++] = y2;
          array[startIndex++] = n1;
          array[startIndex++] = n2;
          array[startIndex++] = color;
          array[startIndex++] = edgeIndex;
          array[startIndex++] = radius;
        }
      }, {
        key: "setUniforms",
        value: function setUniforms(params, _ref) {
          var gl = _ref.gl, uniformLocations = _ref.uniformLocations;
          var u_matrix = uniformLocations.u_matrix, u_zoomRatio = uniformLocations.u_zoomRatio, u_feather = uniformLocations.u_feather, u_pixelRatio = uniformLocations.u_pixelRatio, u_correctionRatio = uniformLocations.u_correctionRatio, u_sizeRatio = uniformLocations.u_sizeRatio, u_minEdgeThickness = uniformLocations.u_minEdgeThickness, u_lengthToThicknessRatio = uniformLocations.u_lengthToThicknessRatio;
          gl.uniformMatrix3fv(u_matrix, false, params.matrix);
          gl.uniform1f(u_zoomRatio, params.zoomRatio);
          gl.uniform1f(u_sizeRatio, params.sizeRatio);
          gl.uniform1f(u_correctionRatio, params.correctionRatio);
          gl.uniform1f(u_pixelRatio, params.pixelRatio);
          gl.uniform1f(u_feather, params.antiAliasingFeather);
          gl.uniform1f(u_minEdgeThickness, params.minEdgeThickness);
          gl.uniform1f(u_lengthToThicknessRatio, options.lengthToThicknessRatio);
        }
      }]);
    })(EdgeProgram);
  }
  var EdgeClampedProgram = createEdgeClampedProgram();
  function createEdgeArrowProgram(inputOptions) {
    return createEdgeCompoundProgram([createEdgeClampedProgram(inputOptions), createEdgeArrowHeadProgram(inputOptions)]);
  }
  var EdgeArrowProgram = createEdgeArrowProgram();
  var EdgeArrowProgram$1 = EdgeArrowProgram;
  var SHADER_SOURCE = (
    /*glsl*/
    `
attribute vec4 a_id;
attribute vec4 a_color;
attribute vec2 a_normal;
attribute float a_normalCoef;
attribute vec2 a_positionStart;
attribute vec2 a_positionEnd;
attribute float a_positionCoef;

uniform mat3 u_matrix;
uniform float u_sizeRatio;
uniform float u_zoomRatio;
uniform float u_pixelRatio;
uniform float u_correctionRatio;
uniform float u_minEdgeThickness;
uniform float u_feather;

varying vec4 v_color;
varying vec2 v_normal;
varying float v_thickness;
varying float v_feather;

const float bias = 255.0 / 254.0;

void main() {
  float minThickness = u_minEdgeThickness;

  vec2 normal = a_normal * a_normalCoef;
  vec2 position = a_positionStart * (1.0 - a_positionCoef) + a_positionEnd * a_positionCoef;

  float normalLength = length(normal);
  vec2 unitNormal = normal / normalLength;

  // We require edges to be at least "minThickness" pixels thick *on screen*
  // (so we need to compensate the size ratio):
  float pixelsThickness = max(normalLength, minThickness * u_sizeRatio);

  // Then, we need to retrieve the normalized thickness of the edge in the WebGL
  // referential (in a ([0, 1], [0, 1]) space), using our "magic" correction
  // ratio:
  float webGLThickness = pixelsThickness * u_correctionRatio / u_sizeRatio;

  // Here is the proper position of the vertex
  gl_Position = vec4((u_matrix * vec3(position + unitNormal * webGLThickness, 1)).xy, 0, 1);

  // For the fragment shader though, we need a thickness that takes the "magic"
  // correction ratio into account (as in webGLThickness), but so that the
  // antialiasing effect does not depend on the zoom level. So here's yet
  // another thickness version:
  v_thickness = webGLThickness / u_zoomRatio;

  v_normal = unitNormal;

  v_feather = u_feather * u_correctionRatio / u_zoomRatio / u_pixelRatio * 2.0;

  #ifdef PICKING_MODE
  // For picking mode, we use the ID as the color:
  v_color = a_id;
  #else
  // For normal mode, we use the color:
  v_color = a_color;
  #endif

  v_color.a *= bias;
}
`
  );
  var VERTEX_SHADER_SOURCE = SHADER_SOURCE;
  var _WebGLRenderingContex = WebGLRenderingContext;
  var UNSIGNED_BYTE = _WebGLRenderingContex.UNSIGNED_BYTE;
  var FLOAT = _WebGLRenderingContex.FLOAT;
  var UNIFORMS = ["u_matrix", "u_zoomRatio", "u_sizeRatio", "u_correctionRatio", "u_pixelRatio", "u_feather", "u_minEdgeThickness"];
  var EdgeRectangleProgram = /* @__PURE__ */ (function(_EdgeProgram) {
    function EdgeRectangleProgram2() {
      _classCallCheck(this, EdgeRectangleProgram2);
      return _callSuper(this, EdgeRectangleProgram2, arguments);
    }
    _inherits(EdgeRectangleProgram2, _EdgeProgram);
    return _createClass(EdgeRectangleProgram2, [{
      key: "getDefinition",
      value: function getDefinition() {
        return {
          VERTICES: 6,
          VERTEX_SHADER_SOURCE,
          FRAGMENT_SHADER_SOURCE,
          METHOD: WebGLRenderingContext.TRIANGLES,
          UNIFORMS,
          ATTRIBUTES: [{
            name: "a_positionStart",
            size: 2,
            type: FLOAT
          }, {
            name: "a_positionEnd",
            size: 2,
            type: FLOAT
          }, {
            name: "a_normal",
            size: 2,
            type: FLOAT
          }, {
            name: "a_color",
            size: 4,
            type: UNSIGNED_BYTE,
            normalized: true
          }, {
            name: "a_id",
            size: 4,
            type: UNSIGNED_BYTE,
            normalized: true
          }],
          CONSTANT_ATTRIBUTES: [
            // If 0, then position will be a_positionStart
            // If 2, then position will be a_positionEnd
            {
              name: "a_positionCoef",
              size: 1,
              type: FLOAT
            },
            {
              name: "a_normalCoef",
              size: 1,
              type: FLOAT
            }
          ],
          CONSTANT_DATA: [[0, 1], [0, -1], [1, 1], [1, 1], [0, -1], [1, -1]]
        };
      }
    }, {
      key: "processVisibleItem",
      value: function processVisibleItem(edgeIndex, startIndex, sourceData, targetData, data) {
        var thickness = data.size || 1;
        var x1 = sourceData.x;
        var y1 = sourceData.y;
        var x2 = targetData.x;
        var y2 = targetData.y;
        var color = floatColor(data.color);
        var dx = x2 - x1;
        var dy = y2 - y1;
        var len = dx * dx + dy * dy;
        var n1 = 0;
        var n2 = 0;
        if (len) {
          len = 1 / Math.sqrt(len);
          n1 = -dy * len * thickness;
          n2 = dx * len * thickness;
        }
        var array = this.array;
        array[startIndex++] = x1;
        array[startIndex++] = y1;
        array[startIndex++] = x2;
        array[startIndex++] = y2;
        array[startIndex++] = n1;
        array[startIndex++] = n2;
        array[startIndex++] = color;
        array[startIndex++] = edgeIndex;
      }
    }, {
      key: "setUniforms",
      value: function setUniforms(params, _ref) {
        var gl = _ref.gl, uniformLocations = _ref.uniformLocations;
        var u_matrix = uniformLocations.u_matrix, u_zoomRatio = uniformLocations.u_zoomRatio, u_feather = uniformLocations.u_feather, u_pixelRatio = uniformLocations.u_pixelRatio, u_correctionRatio = uniformLocations.u_correctionRatio, u_sizeRatio = uniformLocations.u_sizeRatio, u_minEdgeThickness = uniformLocations.u_minEdgeThickness;
        gl.uniformMatrix3fv(u_matrix, false, params.matrix);
        gl.uniform1f(u_zoomRatio, params.zoomRatio);
        gl.uniform1f(u_sizeRatio, params.sizeRatio);
        gl.uniform1f(u_correctionRatio, params.correctionRatio);
        gl.uniform1f(u_pixelRatio, params.pixelRatio);
        gl.uniform1f(u_feather, params.antiAliasingFeather);
        gl.uniform1f(u_minEdgeThickness, params.minEdgeThickness);
      }
    }]);
  })(EdgeProgram);

  // node_modules/sigma/types/dist/sigma-types.esm.js
  var import_events = __toESM(require_events());
  var TypedEventEmitter = /* @__PURE__ */ (function(_ref) {
    function TypedEventEmitter2() {
      var _this;
      _classCallCheck(this, TypedEventEmitter2);
      _this = _callSuper(this, TypedEventEmitter2);
      _this.rawEmitter = _this;
      return _this;
    }
    _inherits(TypedEventEmitter2, _ref);
    return _createClass(TypedEventEmitter2);
  })(import_events.EventEmitter);

  // node_modules/sigma/dist/normalization-be445518.esm.js
  var import_is_graph = __toESM(require_is_graph());
  var linear = function linear2(k) {
    return k;
  };
  var quadraticIn = function quadraticIn2(k) {
    return k * k;
  };
  var quadraticOut = function quadraticOut2(k) {
    return k * (2 - k);
  };
  var quadraticInOut = function quadraticInOut2(k) {
    if ((k *= 2) < 1) return 0.5 * k * k;
    return -0.5 * (--k * (k - 2) - 1);
  };
  var cubicIn = function cubicIn2(k) {
    return k * k * k;
  };
  var cubicOut = function cubicOut2(k) {
    return --k * k * k + 1;
  };
  var cubicInOut = function cubicInOut2(k) {
    if ((k *= 2) < 1) return 0.5 * k * k * k;
    return 0.5 * ((k -= 2) * k * k + 2);
  };
  var easings = {
    linear,
    quadraticIn,
    quadraticOut,
    quadraticInOut,
    cubicIn,
    cubicOut,
    cubicInOut
  };
  var ANIMATE_DEFAULTS = {
    easing: "quadraticInOut",
    duration: 150
  };
  function identity() {
    return Float32Array.of(1, 0, 0, 0, 1, 0, 0, 0, 1);
  }
  function scale(m, x, y) {
    m[0] = x;
    m[4] = typeof y === "number" ? y : x;
    return m;
  }
  function rotate(m, r) {
    var s = Math.sin(r), c = Math.cos(r);
    m[0] = c;
    m[1] = s;
    m[3] = -s;
    m[4] = c;
    return m;
  }
  function translate(m, x, y) {
    m[6] = x;
    m[7] = y;
    return m;
  }
  function multiply(a, b) {
    var a00 = a[0], a01 = a[1], a02 = a[2];
    var a10 = a[3], a11 = a[4], a12 = a[5];
    var a20 = a[6], a21 = a[7], a22 = a[8];
    var b00 = b[0], b01 = b[1], b02 = b[2];
    var b10 = b[3], b11 = b[4], b12 = b[5];
    var b20 = b[6], b21 = b[7], b22 = b[8];
    a[0] = b00 * a00 + b01 * a10 + b02 * a20;
    a[1] = b00 * a01 + b01 * a11 + b02 * a21;
    a[2] = b00 * a02 + b01 * a12 + b02 * a22;
    a[3] = b10 * a00 + b11 * a10 + b12 * a20;
    a[4] = b10 * a01 + b11 * a11 + b12 * a21;
    a[5] = b10 * a02 + b11 * a12 + b12 * a22;
    a[6] = b20 * a00 + b21 * a10 + b22 * a20;
    a[7] = b20 * a01 + b21 * a11 + b22 * a21;
    a[8] = b20 * a02 + b21 * a12 + b22 * a22;
    return a;
  }
  function multiplyVec2(a, b) {
    var z = arguments.length > 2 && arguments[2] !== void 0 ? arguments[2] : 1;
    var a00 = a[0];
    var a01 = a[1];
    var a10 = a[3];
    var a11 = a[4];
    var a20 = a[6];
    var a21 = a[7];
    var b0 = b.x;
    var b1 = b.y;
    return {
      x: b0 * a00 + b1 * a10 + a20 * z,
      y: b0 * a01 + b1 * a11 + a21 * z
    };
  }
  function getCorrectionRatio(viewportDimensions, graphDimensions) {
    var viewportRatio = viewportDimensions.height / viewportDimensions.width;
    var graphRatio = graphDimensions.height / graphDimensions.width;
    if (viewportRatio < 1 && graphRatio > 1 || viewportRatio > 1 && graphRatio < 1) {
      return 1;
    }
    return Math.min(Math.max(graphRatio, 1 / graphRatio), Math.max(1 / viewportRatio, viewportRatio));
  }
  function matrixFromCamera(state, viewportDimensions, graphDimensions, padding, inverse) {
    var angle = state.angle, ratio = state.ratio, x = state.x, y = state.y;
    var width = viewportDimensions.width, height = viewportDimensions.height;
    var matrix = identity();
    var smallestDimension = Math.min(width, height) - 2 * padding;
    var correctionRatio = getCorrectionRatio(viewportDimensions, graphDimensions);
    if (!inverse) {
      multiply(matrix, scale(identity(), 2 * (smallestDimension / width) * correctionRatio, 2 * (smallestDimension / height) * correctionRatio));
      multiply(matrix, rotate(identity(), -angle));
      multiply(matrix, scale(identity(), 1 / ratio));
      multiply(matrix, translate(identity(), -x, -y));
    } else {
      multiply(matrix, translate(identity(), x, y));
      multiply(matrix, scale(identity(), ratio));
      multiply(matrix, rotate(identity(), angle));
      multiply(matrix, scale(identity(), width / smallestDimension / 2 / correctionRatio, height / smallestDimension / 2 / correctionRatio));
    }
    return matrix;
  }
  function getMatrixImpact(matrix, cameraState, viewportDimensions) {
    var _multiplyVec = multiplyVec2(matrix, {
      x: Math.cos(cameraState.angle),
      y: Math.sin(cameraState.angle)
    }, 0), x = _multiplyVec.x, y = _multiplyVec.y;
    return 1 / Math.sqrt(Math.pow(x, 2) + Math.pow(y, 2)) / viewportDimensions.width;
  }
  function graphExtent(graph) {
    if (!graph.order) return {
      x: [0, 1],
      y: [0, 1]
    };
    var xMin = Infinity;
    var xMax = -Infinity;
    var yMin = Infinity;
    var yMax = -Infinity;
    graph.forEachNode(function(_, attr) {
      var x = attr.x, y = attr.y;
      if (x < xMin) xMin = x;
      if (x > xMax) xMax = x;
      if (y < yMin) yMin = y;
      if (y > yMax) yMax = y;
    });
    return {
      x: [xMin, xMax],
      y: [yMin, yMax]
    };
  }
  function validateGraph(graph) {
    if (!(0, import_is_graph.default)(graph)) throw new Error("Sigma: invalid graph instance.");
    graph.forEachNode(function(key, attributes) {
      if (!Number.isFinite(attributes.x) || !Number.isFinite(attributes.y)) {
        throw new Error("Sigma: Coordinates of node ".concat(key, " are invalid. A node must have a numeric 'x' and 'y' attribute."));
      }
    });
  }
  function createElement(tag, style, attributes) {
    var element = document.createElement(tag);
    if (style) {
      for (var k in style) {
        element.style[k] = style[k];
      }
    }
    if (attributes) {
      for (var _k in attributes) {
        element.setAttribute(_k, attributes[_k]);
      }
    }
    return element;
  }
  function getPixelRatio() {
    if (typeof window.devicePixelRatio !== "undefined") return window.devicePixelRatio;
    return 1;
  }
  function zIndexOrdering(_extent, getter, elements) {
    return elements.sort(function(a, b) {
      var zA = getter(a) || 0, zB = getter(b) || 0;
      if (zA < zB) return -1;
      if (zA > zB) return 1;
      return 0;
    });
  }
  function createNormalizationFunction(extent) {
    var _extent$x = _slicedToArray(extent.x, 2), minX = _extent$x[0], maxX = _extent$x[1], _extent$y = _slicedToArray(extent.y, 2), minY = _extent$y[0], maxY = _extent$y[1];
    var ratio = Math.max(maxX - minX, maxY - minY), dX = (maxX + minX) / 2, dY = (maxY + minY) / 2;
    if (ratio === 0 || Math.abs(ratio) === Infinity || isNaN(ratio)) ratio = 1;
    if (isNaN(dX)) dX = 0;
    if (isNaN(dY)) dY = 0;
    var fn = function fn2(data) {
      return {
        x: 0.5 + (data.x - dX) / ratio,
        y: 0.5 + (data.y - dY) / ratio
      };
    };
    fn.applyTo = function(data) {
      data.x = 0.5 + (data.x - dX) / ratio;
      data.y = 0.5 + (data.y - dY) / ratio;
    };
    fn.inverse = function(data) {
      return {
        x: dX + ratio * (data.x - 0.5),
        y: dY + ratio * (data.y - 0.5)
      };
    };
    fn.ratio = ratio;
    return fn;
  }

  // node_modules/sigma/dist/data-11df7124.esm.js
  function _typeof(o) {
    "@babel/helpers - typeof";
    return _typeof = "function" == typeof Symbol && "symbol" == typeof Symbol.iterator ? function(o2) {
      return typeof o2;
    } : function(o2) {
      return o2 && "function" == typeof Symbol && o2.constructor === Symbol && o2 !== Symbol.prototype ? "symbol" : typeof o2;
    }, _typeof(o);
  }
  function extend(array, values) {
    var l2 = values.size;
    if (l2 === 0) return;
    var l1 = array.length;
    array.length += l2;
    var i = 0;
    values.forEach(function(value) {
      array[l1 + i] = value;
      i++;
    });
  }
  function assign(target) {
    target = target || {};
    for (var i = 0, l = arguments.length <= 1 ? 0 : arguments.length - 1; i < l; i++) {
      var o = i + 1 < 1 || arguments.length <= i + 1 ? void 0 : arguments[i + 1];
      if (!o) continue;
      Object.assign(target, o);
    }
    return target;
  }

  // node_modules/sigma/settings/dist/sigma-settings.esm.js
  var DEFAULT_SETTINGS = {
    // Performance
    hideEdgesOnMove: false,
    hideLabelsOnMove: false,
    renderLabels: true,
    renderEdgeLabels: false,
    enableEdgeEvents: false,
    // Component rendering
    defaultNodeColor: "#999",
    defaultNodeType: "circle",
    defaultEdgeColor: "#ccc",
    defaultEdgeType: "line",
    labelFont: "Arial",
    labelSize: 14,
    labelWeight: "normal",
    labelColor: {
      color: "#000"
    },
    edgeLabelFont: "Arial",
    edgeLabelSize: 14,
    edgeLabelWeight: "normal",
    edgeLabelColor: {
      attribute: "color"
    },
    stagePadding: 30,
    defaultDrawEdgeLabel: drawStraightEdgeLabel,
    defaultDrawNodeLabel: drawDiscNodeLabel,
    defaultDrawNodeHover: drawDiscNodeHover,
    minEdgeThickness: 1.7,
    antiAliasingFeather: 1,
    // Mouse and touch settings
    dragTimeout: 100,
    draggedEventsTolerance: 3,
    inertiaDuration: 200,
    inertiaRatio: 3,
    zoomDuration: 250,
    zoomingRatio: 1.7,
    doubleClickTimeout: 300,
    doubleClickZoomingRatio: 2.2,
    doubleClickZoomingDuration: 200,
    tapMoveTolerance: 10,
    // Size and scaling
    zoomToSizeRatioFunction: Math.sqrt,
    itemSizesReference: "screen",
    autoRescale: true,
    autoCenter: true,
    // Labels
    labelDensity: 1,
    labelGridCellSize: 100,
    labelRenderedSizeThreshold: 6,
    // Reducers
    nodeReducer: null,
    edgeReducer: null,
    // Features
    zIndex: false,
    minCameraRatio: null,
    maxCameraRatio: null,
    enableCameraZooming: true,
    enableCameraPanning: true,
    enableCameraRotation: true,
    cameraPanBoundaries: null,
    // Lifecycle
    allowInvalidContainer: false,
    // Program classes
    nodeProgramClasses: {},
    nodeHoverProgramClasses: {},
    edgeProgramClasses: {}
  };
  var DEFAULT_NODE_PROGRAM_CLASSES = {
    circle: NodeCircleProgram
  };
  var DEFAULT_EDGE_PROGRAM_CLASSES = {
    arrow: EdgeArrowProgram$1,
    line: EdgeRectangleProgram
  };
  function validateSettings(settings) {
    if (typeof settings.labelDensity !== "number" || settings.labelDensity < 0) {
      throw new Error("Settings: invalid `labelDensity`. Expecting a positive number.");
    }
    var minCameraRatio = settings.minCameraRatio, maxCameraRatio = settings.maxCameraRatio;
    if (typeof minCameraRatio === "number" && typeof maxCameraRatio === "number" && maxCameraRatio < minCameraRatio) {
      throw new Error("Settings: invalid camera ratio boundaries. Expecting `maxCameraRatio` to be greater than `minCameraRatio`.");
    }
  }
  function resolveSettings(settings) {
    var resolvedSettings = assign({}, DEFAULT_SETTINGS, settings);
    resolvedSettings.nodeProgramClasses = assign({}, DEFAULT_NODE_PROGRAM_CLASSES, resolvedSettings.nodeProgramClasses);
    resolvedSettings.edgeProgramClasses = assign({}, DEFAULT_EDGE_PROGRAM_CLASSES, resolvedSettings.edgeProgramClasses);
    return resolvedSettings;
  }

  // node_modules/sigma/dist/sigma.esm.js
  var import_events2 = __toESM(require_events());
  var import_is_graph2 = __toESM(require_is_graph());
  var DEFAULT_ZOOMING_RATIO = 1.5;
  var Camera = /* @__PURE__ */ (function(_TypedEventEmitter) {
    function Camera2() {
      var _this;
      _classCallCheck(this, Camera2);
      _this = _callSuper(this, Camera2);
      _defineProperty(_this, "x", 0.5);
      _defineProperty(_this, "y", 0.5);
      _defineProperty(_this, "angle", 0);
      _defineProperty(_this, "ratio", 1);
      _defineProperty(_this, "minRatio", null);
      _defineProperty(_this, "maxRatio", null);
      _defineProperty(_this, "enabledZooming", true);
      _defineProperty(_this, "enabledPanning", true);
      _defineProperty(_this, "enabledRotation", true);
      _defineProperty(_this, "clean", null);
      _defineProperty(_this, "nextFrame", null);
      _defineProperty(_this, "previousState", null);
      _defineProperty(_this, "enabled", true);
      _this.previousState = _this.getState();
      return _this;
    }
    _inherits(Camera2, _TypedEventEmitter);
    return _createClass(Camera2, [{
      key: "enable",
      value: (
        /**
         * Method used to enable the camera.
         */
        function enable() {
          this.enabled = true;
          return this;
        }
      )
      /**
       * Method used to disable the camera.
       */
    }, {
      key: "disable",
      value: function disable() {
        this.enabled = false;
        return this;
      }
      /**
       * Method used to retrieve the camera's current state.
       */
    }, {
      key: "getState",
      value: function getState() {
        return {
          x: this.x,
          y: this.y,
          angle: this.angle,
          ratio: this.ratio
        };
      }
      /**
       * Method used to check whether the camera has the given state.
       */
    }, {
      key: "hasState",
      value: function hasState(state) {
        return this.x === state.x && this.y === state.y && this.ratio === state.ratio && this.angle === state.angle;
      }
      /**
       * Method used to retrieve the camera's previous state.
       */
    }, {
      key: "getPreviousState",
      value: function getPreviousState() {
        var state = this.previousState;
        if (!state) return null;
        return {
          x: state.x,
          y: state.y,
          angle: state.angle,
          ratio: state.ratio
        };
      }
      /**
       * Method used to check minRatio and maxRatio values.
       */
    }, {
      key: "getBoundedRatio",
      value: function getBoundedRatio(ratio) {
        var r = ratio;
        if (typeof this.minRatio === "number") r = Math.max(r, this.minRatio);
        if (typeof this.maxRatio === "number") r = Math.min(r, this.maxRatio);
        return r;
      }
      /**
       * Method used to check various things to return a legit state candidate.
       */
    }, {
      key: "validateState",
      value: function validateState(state) {
        var validatedState = {};
        if (this.enabledPanning && typeof state.x === "number") validatedState.x = state.x;
        if (this.enabledPanning && typeof state.y === "number") validatedState.y = state.y;
        if (this.enabledZooming && typeof state.ratio === "number") validatedState.ratio = this.getBoundedRatio(state.ratio);
        if (this.enabledRotation && typeof state.angle === "number") validatedState.angle = state.angle;
        return this.clean ? this.clean(_objectSpread2(_objectSpread2({}, this.getState()), validatedState)) : validatedState;
      }
      /**
       * Method used to check whether the camera is currently being animated.
       */
    }, {
      key: "isAnimated",
      value: function isAnimated() {
        return !!this.nextFrame;
      }
      /**
       * Method used to set the camera's state.
       */
    }, {
      key: "setState",
      value: function setState(state) {
        if (!this.enabled) return this;
        this.previousState = this.getState();
        var validState = this.validateState(state);
        if (typeof validState.x === "number") this.x = validState.x;
        if (typeof validState.y === "number") this.y = validState.y;
        if (typeof validState.ratio === "number") this.ratio = validState.ratio;
        if (typeof validState.angle === "number") this.angle = validState.angle;
        if (!this.hasState(this.previousState)) this.emit("updated", this.getState());
        return this;
      }
      /**
       * Method used to update the camera's state using a function.
       */
    }, {
      key: "updateState",
      value: function updateState(updater) {
        this.setState(updater(this.getState()));
        return this;
      }
      /**
       * Method used to animate the camera.
       */
    }, {
      key: "animate",
      value: function animate(state) {
        var _this2 = this;
        var opts = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : {};
        var callback = arguments.length > 2 ? arguments[2] : void 0;
        if (!callback) return new Promise(function(resolve) {
          return _this2.animate(state, opts, resolve);
        });
        if (!this.enabled) return;
        var options = _objectSpread2(_objectSpread2({}, ANIMATE_DEFAULTS), opts);
        var validState = this.validateState(state);
        var easing = typeof options.easing === "function" ? options.easing : easings[options.easing];
        var start = Date.now(), initialState = this.getState();
        var _fn = function fn() {
          var t = (Date.now() - start) / options.duration;
          if (t >= 1) {
            _this2.nextFrame = null;
            _this2.setState(validState);
            if (_this2.animationCallback) {
              _this2.animationCallback.call(null);
              _this2.animationCallback = void 0;
            }
            return;
          }
          var coefficient = easing(t);
          var newState = {};
          if (typeof validState.x === "number") newState.x = initialState.x + (validState.x - initialState.x) * coefficient;
          if (typeof validState.y === "number") newState.y = initialState.y + (validState.y - initialState.y) * coefficient;
          if (_this2.enabledRotation && typeof validState.angle === "number") newState.angle = initialState.angle + (validState.angle - initialState.angle) * coefficient;
          if (typeof validState.ratio === "number") newState.ratio = initialState.ratio + (validState.ratio - initialState.ratio) * coefficient;
          _this2.setState(newState);
          _this2.nextFrame = requestAnimationFrame(_fn);
        };
        if (this.nextFrame) {
          cancelAnimationFrame(this.nextFrame);
          if (this.animationCallback) this.animationCallback.call(null);
          this.nextFrame = requestAnimationFrame(_fn);
        } else {
          _fn();
        }
        this.animationCallback = callback;
      }
      /**
       * Method used to zoom the camera.
       */
    }, {
      key: "animatedZoom",
      value: function animatedZoom(factorOrOptions) {
        if (!factorOrOptions) return this.animate({
          ratio: this.ratio / DEFAULT_ZOOMING_RATIO
        });
        if (typeof factorOrOptions === "number") return this.animate({
          ratio: this.ratio / factorOrOptions
        });
        return this.animate({
          ratio: this.ratio / (factorOrOptions.factor || DEFAULT_ZOOMING_RATIO)
        }, factorOrOptions);
      }
      /**
       * Method used to unzoom the camera.
       */
    }, {
      key: "animatedUnzoom",
      value: function animatedUnzoom(factorOrOptions) {
        if (!factorOrOptions) return this.animate({
          ratio: this.ratio * DEFAULT_ZOOMING_RATIO
        });
        if (typeof factorOrOptions === "number") return this.animate({
          ratio: this.ratio * factorOrOptions
        });
        return this.animate({
          ratio: this.ratio * (factorOrOptions.factor || DEFAULT_ZOOMING_RATIO)
        }, factorOrOptions);
      }
      /**
       * Method used to reset the camera.
       */
    }, {
      key: "animatedReset",
      value: function animatedReset(options) {
        return this.animate({
          x: 0.5,
          y: 0.5,
          ratio: 1,
          angle: 0
        }, options);
      }
      /**
       * Returns a new Camera instance, with the same state as the current camera.
       */
    }, {
      key: "copy",
      value: function copy() {
        return Camera2.from(this.getState());
      }
    }], [{
      key: "from",
      value: function from(state) {
        var camera = new Camera2();
        return camera.setState(state);
      }
    }]);
  })(TypedEventEmitter);
  function getPosition(e, dom) {
    var bbox = dom.getBoundingClientRect();
    return {
      x: e.clientX - bbox.left,
      y: e.clientY - bbox.top
    };
  }
  function getMouseCoords(e, dom) {
    var res = _objectSpread2(_objectSpread2({}, getPosition(e, dom)), {}, {
      sigmaDefaultPrevented: false,
      preventSigmaDefault: function preventSigmaDefault() {
        res.sigmaDefaultPrevented = true;
      },
      original: e
    });
    return res;
  }
  function cleanMouseCoords(e) {
    var res = "x" in e ? e : _objectSpread2(_objectSpread2({}, e.touches[0] || e.previousTouches[0]), {}, {
      original: e.original,
      sigmaDefaultPrevented: e.sigmaDefaultPrevented,
      preventSigmaDefault: function preventSigmaDefault() {
        e.sigmaDefaultPrevented = true;
        res.sigmaDefaultPrevented = true;
      }
    });
    return res;
  }
  function getWheelCoords(e, dom) {
    return _objectSpread2(_objectSpread2({}, getMouseCoords(e, dom)), {}, {
      delta: getWheelDelta(e)
    });
  }
  var MAX_TOUCHES = 2;
  function getTouchesArray(touches) {
    var arr = [];
    for (var i = 0, l = Math.min(touches.length, MAX_TOUCHES); i < l; i++) arr.push(touches[i]);
    return arr;
  }
  function getTouchCoords(e, previousTouches, dom) {
    var res = {
      touches: getTouchesArray(e.touches).map(function(touch) {
        return getPosition(touch, dom);
      }),
      previousTouches: previousTouches.map(function(touch) {
        return getPosition(touch, dom);
      }),
      sigmaDefaultPrevented: false,
      preventSigmaDefault: function preventSigmaDefault() {
        res.sigmaDefaultPrevented = true;
      },
      original: e
    };
    return res;
  }
  function getWheelDelta(e) {
    if (typeof e.deltaY !== "undefined") return e.deltaY * -3 / 360;
    if (typeof e.detail !== "undefined") return e.detail / -9;
    throw new Error("Captor: could not extract delta from event.");
  }
  var Captor = /* @__PURE__ */ (function(_TypedEventEmitter) {
    function Captor2(container, renderer) {
      var _this;
      _classCallCheck(this, Captor2);
      _this = _callSuper(this, Captor2);
      _this.container = container;
      _this.renderer = renderer;
      return _this;
    }
    _inherits(Captor2, _TypedEventEmitter);
    return _createClass(Captor2);
  })(TypedEventEmitter);
  var MOUSE_SETTINGS_KEYS = ["doubleClickTimeout", "doubleClickZoomingDuration", "doubleClickZoomingRatio", "dragTimeout", "draggedEventsTolerance", "inertiaDuration", "inertiaRatio", "zoomDuration", "zoomingRatio"];
  var DEFAULT_MOUSE_SETTINGS = MOUSE_SETTINGS_KEYS.reduce(function(iter, key) {
    return _objectSpread2(_objectSpread2({}, iter), {}, _defineProperty({}, key, DEFAULT_SETTINGS[key]));
  }, {});
  var MouseCaptor = /* @__PURE__ */ (function(_Captor) {
    function MouseCaptor2(container, renderer) {
      var _this;
      _classCallCheck(this, MouseCaptor2);
      _this = _callSuper(this, MouseCaptor2, [container, renderer]);
      _defineProperty(_this, "enabled", true);
      _defineProperty(_this, "draggedEvents", 0);
      _defineProperty(_this, "downStartTime", null);
      _defineProperty(_this, "lastMouseX", null);
      _defineProperty(_this, "lastMouseY", null);
      _defineProperty(_this, "isMouseDown", false);
      _defineProperty(_this, "isMoving", false);
      _defineProperty(_this, "movingTimeout", null);
      _defineProperty(_this, "startCameraState", null);
      _defineProperty(_this, "clicks", 0);
      _defineProperty(_this, "doubleClickTimeout", null);
      _defineProperty(_this, "currentWheelDirection", 0);
      _defineProperty(_this, "settings", DEFAULT_MOUSE_SETTINGS);
      _this.handleClick = _this.handleClick.bind(_this);
      _this.handleRightClick = _this.handleRightClick.bind(_this);
      _this.handleDown = _this.handleDown.bind(_this);
      _this.handleUp = _this.handleUp.bind(_this);
      _this.handleMove = _this.handleMove.bind(_this);
      _this.handleWheel = _this.handleWheel.bind(_this);
      _this.handleLeave = _this.handleLeave.bind(_this);
      _this.handleEnter = _this.handleEnter.bind(_this);
      container.addEventListener("click", _this.handleClick, {
        capture: false
      });
      container.addEventListener("contextmenu", _this.handleRightClick, {
        capture: false
      });
      container.addEventListener("mousedown", _this.handleDown, {
        capture: false
      });
      container.addEventListener("wheel", _this.handleWheel, {
        capture: false
      });
      container.addEventListener("mouseleave", _this.handleLeave, {
        capture: false
      });
      container.addEventListener("mouseenter", _this.handleEnter, {
        capture: false
      });
      document.addEventListener("mousemove", _this.handleMove, {
        capture: false
      });
      document.addEventListener("mouseup", _this.handleUp, {
        capture: false
      });
      return _this;
    }
    _inherits(MouseCaptor2, _Captor);
    return _createClass(MouseCaptor2, [{
      key: "kill",
      value: function kill() {
        var container = this.container;
        container.removeEventListener("click", this.handleClick);
        container.removeEventListener("contextmenu", this.handleRightClick);
        container.removeEventListener("mousedown", this.handleDown);
        container.removeEventListener("wheel", this.handleWheel);
        container.removeEventListener("mouseleave", this.handleLeave);
        container.removeEventListener("mouseenter", this.handleEnter);
        document.removeEventListener("mousemove", this.handleMove);
        document.removeEventListener("mouseup", this.handleUp);
      }
    }, {
      key: "handleClick",
      value: function handleClick(e) {
        var _this2 = this;
        if (!this.enabled) return;
        this.clicks++;
        if (this.clicks === 2) {
          this.clicks = 0;
          if (typeof this.doubleClickTimeout === "number") {
            clearTimeout(this.doubleClickTimeout);
            this.doubleClickTimeout = null;
          }
          return this.handleDoubleClick(e);
        }
        setTimeout(function() {
          _this2.clicks = 0;
          _this2.doubleClickTimeout = null;
        }, this.settings.doubleClickTimeout);
        if (this.draggedEvents < this.settings.draggedEventsTolerance) this.emit("click", getMouseCoords(e, this.container));
      }
    }, {
      key: "handleRightClick",
      value: function handleRightClick(e) {
        if (!this.enabled) return;
        this.emit("rightClick", getMouseCoords(e, this.container));
      }
    }, {
      key: "handleDoubleClick",
      value: function handleDoubleClick(e) {
        if (!this.enabled) return;
        e.preventDefault();
        e.stopPropagation();
        var mouseCoords = getMouseCoords(e, this.container);
        this.emit("doubleClick", mouseCoords);
        if (mouseCoords.sigmaDefaultPrevented) return;
        var camera = this.renderer.getCamera();
        var newRatio = camera.getBoundedRatio(camera.getState().ratio / this.settings.doubleClickZoomingRatio);
        camera.animate(this.renderer.getViewportZoomedState(getPosition(e, this.container), newRatio), {
          easing: "quadraticInOut",
          duration: this.settings.doubleClickZoomingDuration
        });
      }
    }, {
      key: "handleDown",
      value: function handleDown(e) {
        if (!this.enabled) return;
        if (e.button === 0) {
          this.startCameraState = this.renderer.getCamera().getState();
          var _getPosition = getPosition(e, this.container), x = _getPosition.x, y = _getPosition.y;
          this.lastMouseX = x;
          this.lastMouseY = y;
          this.draggedEvents = 0;
          this.downStartTime = Date.now();
          this.isMouseDown = true;
        }
        this.emit("mousedown", getMouseCoords(e, this.container));
      }
    }, {
      key: "handleUp",
      value: function handleUp(e) {
        var _this3 = this;
        if (!this.enabled || !this.isMouseDown) return;
        var camera = this.renderer.getCamera();
        this.isMouseDown = false;
        if (typeof this.movingTimeout === "number") {
          clearTimeout(this.movingTimeout);
          this.movingTimeout = null;
        }
        var _getPosition2 = getPosition(e, this.container), x = _getPosition2.x, y = _getPosition2.y;
        var cameraState = camera.getState(), previousCameraState = camera.getPreviousState() || {
          x: 0,
          y: 0
        };
        if (this.isMoving) {
          camera.animate({
            x: cameraState.x + this.settings.inertiaRatio * (cameraState.x - previousCameraState.x),
            y: cameraState.y + this.settings.inertiaRatio * (cameraState.y - previousCameraState.y)
          }, {
            duration: this.settings.inertiaDuration,
            easing: "quadraticOut"
          });
        } else if (this.lastMouseX !== x || this.lastMouseY !== y) {
          camera.setState({
            x: cameraState.x,
            y: cameraState.y
          });
        }
        this.isMoving = false;
        setTimeout(function() {
          var shouldRefresh = _this3.draggedEvents > 0;
          _this3.draggedEvents = 0;
          if (shouldRefresh && _this3.renderer.getSetting("hideEdgesOnMove")) _this3.renderer.refresh();
        }, 0);
        this.emit("mouseup", getMouseCoords(e, this.container));
      }
    }, {
      key: "handleMove",
      value: function handleMove(e) {
        var _this4 = this;
        if (!this.enabled) return;
        var mouseCoords = getMouseCoords(e, this.container);
        this.emit("mousemovebody", mouseCoords);
        if (e.target === this.container || e.composedPath()[0] === this.container) {
          this.emit("mousemove", mouseCoords);
        }
        if (mouseCoords.sigmaDefaultPrevented) return;
        if (this.isMouseDown) {
          this.isMoving = true;
          this.draggedEvents++;
          if (typeof this.movingTimeout === "number") {
            clearTimeout(this.movingTimeout);
          }
          this.movingTimeout = window.setTimeout(function() {
            _this4.movingTimeout = null;
            _this4.isMoving = false;
          }, this.settings.dragTimeout);
          var camera = this.renderer.getCamera();
          var _getPosition3 = getPosition(e, this.container), eX = _getPosition3.x, eY = _getPosition3.y;
          var lastMouse = this.renderer.viewportToFramedGraph({
            x: this.lastMouseX,
            y: this.lastMouseY
          });
          var mouse = this.renderer.viewportToFramedGraph({
            x: eX,
            y: eY
          });
          var offsetX = lastMouse.x - mouse.x, offsetY = lastMouse.y - mouse.y;
          var cameraState = camera.getState();
          var x = cameraState.x + offsetX, y = cameraState.y + offsetY;
          camera.setState({
            x,
            y
          });
          this.lastMouseX = eX;
          this.lastMouseY = eY;
          e.preventDefault();
          e.stopPropagation();
        }
      }
    }, {
      key: "handleLeave",
      value: function handleLeave(e) {
        this.emit("mouseleave", getMouseCoords(e, this.container));
      }
    }, {
      key: "handleEnter",
      value: function handleEnter(e) {
        this.emit("mouseenter", getMouseCoords(e, this.container));
      }
    }, {
      key: "handleWheel",
      value: function handleWheel(e) {
        var _this5 = this;
        var camera = this.renderer.getCamera();
        if (!this.enabled || !camera.enabledZooming) return;
        var delta = getWheelDelta(e);
        if (!delta) return;
        var wheelCoords = getWheelCoords(e, this.container);
        this.emit("wheel", wheelCoords);
        if (wheelCoords.sigmaDefaultPrevented) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        var currentRatio = camera.getState().ratio;
        var ratioDiff = delta > 0 ? 1 / this.settings.zoomingRatio : this.settings.zoomingRatio;
        var newRatio = camera.getBoundedRatio(currentRatio * ratioDiff);
        var wheelDirection = delta > 0 ? 1 : -1;
        var now = Date.now();
        if (currentRatio === newRatio) return;
        e.preventDefault();
        e.stopPropagation();
        if (this.currentWheelDirection === wheelDirection && this.lastWheelTriggerTime && now - this.lastWheelTriggerTime < this.settings.zoomDuration / 5) {
          return;
        }
        camera.animate(this.renderer.getViewportZoomedState(getPosition(e, this.container), newRatio), {
          easing: "quadraticOut",
          duration: this.settings.zoomDuration
        }, function() {
          _this5.currentWheelDirection = 0;
        });
        this.currentWheelDirection = wheelDirection;
        this.lastWheelTriggerTime = now;
      }
    }, {
      key: "setSettings",
      value: function setSettings(settings) {
        this.settings = settings;
      }
    }]);
  })(Captor);
  var TOUCH_SETTINGS_KEYS = ["dragTimeout", "inertiaDuration", "inertiaRatio", "doubleClickTimeout", "doubleClickZoomingRatio", "doubleClickZoomingDuration", "tapMoveTolerance"];
  var DEFAULT_TOUCH_SETTINGS = TOUCH_SETTINGS_KEYS.reduce(function(iter, key) {
    return _objectSpread2(_objectSpread2({}, iter), {}, _defineProperty({}, key, DEFAULT_SETTINGS[key]));
  }, {});
  var TouchCaptor = /* @__PURE__ */ (function(_Captor) {
    function TouchCaptor2(container, renderer) {
      var _this;
      _classCallCheck(this, TouchCaptor2);
      _this = _callSuper(this, TouchCaptor2, [container, renderer]);
      _defineProperty(_this, "enabled", true);
      _defineProperty(_this, "isMoving", false);
      _defineProperty(_this, "hasMoved", false);
      _defineProperty(_this, "touchMode", 0);
      _defineProperty(_this, "startTouchesPositions", []);
      _defineProperty(_this, "lastTouches", []);
      _defineProperty(_this, "lastTap", null);
      _defineProperty(_this, "settings", DEFAULT_TOUCH_SETTINGS);
      _this.handleStart = _this.handleStart.bind(_this);
      _this.handleLeave = _this.handleLeave.bind(_this);
      _this.handleMove = _this.handleMove.bind(_this);
      container.addEventListener("touchstart", _this.handleStart, {
        capture: false
      });
      container.addEventListener("touchcancel", _this.handleLeave, {
        capture: false
      });
      document.addEventListener("touchend", _this.handleLeave, {
        capture: false,
        passive: false
      });
      document.addEventListener("touchmove", _this.handleMove, {
        capture: false,
        passive: false
      });
      return _this;
    }
    _inherits(TouchCaptor2, _Captor);
    return _createClass(TouchCaptor2, [{
      key: "kill",
      value: function kill() {
        var container = this.container;
        container.removeEventListener("touchstart", this.handleStart);
        container.removeEventListener("touchcancel", this.handleLeave);
        document.removeEventListener("touchend", this.handleLeave);
        document.removeEventListener("touchmove", this.handleMove);
      }
    }, {
      key: "getDimensions",
      value: function getDimensions() {
        return {
          width: this.container.offsetWidth,
          height: this.container.offsetHeight
        };
      }
    }, {
      key: "handleStart",
      value: function handleStart(e) {
        var _this2 = this;
        if (!this.enabled) return;
        e.preventDefault();
        var touches = getTouchesArray(e.touches);
        this.touchMode = touches.length;
        this.startCameraState = this.renderer.getCamera().getState();
        this.startTouchesPositions = touches.map(function(touch) {
          return getPosition(touch, _this2.container);
        });
        if (this.touchMode === 2) {
          var _this$startTouchesPos = _slicedToArray(this.startTouchesPositions, 2), _this$startTouchesPos2 = _this$startTouchesPos[0], x0 = _this$startTouchesPos2.x, y0 = _this$startTouchesPos2.y, _this$startTouchesPos3 = _this$startTouchesPos[1], x1 = _this$startTouchesPos3.x, y1 = _this$startTouchesPos3.y;
          this.startTouchesAngle = Math.atan2(y1 - y0, x1 - x0);
          this.startTouchesDistance = Math.sqrt(Math.pow(x1 - x0, 2) + Math.pow(y1 - y0, 2));
        }
        this.emit("touchdown", getTouchCoords(e, this.lastTouches, this.container));
        this.lastTouches = touches;
        this.lastTouchesPositions = this.startTouchesPositions;
      }
    }, {
      key: "handleLeave",
      value: function handleLeave(e) {
        if (!this.enabled || !this.startTouchesPositions.length) return;
        if (e.cancelable) e.preventDefault();
        if (this.movingTimeout) {
          this.isMoving = false;
          clearTimeout(this.movingTimeout);
        }
        switch (this.touchMode) {
          // eslint-disable-next-line @typescript-eslint/ban-ts-comment
          // @ts-ignore
          case 2:
            if (e.touches.length === 1) {
              this.handleStart(e);
              e.preventDefault();
              break;
            }
          /* falls through */
          case 1:
            if (this.isMoving) {
              var camera = this.renderer.getCamera();
              var cameraState = camera.getState(), previousCameraState = camera.getPreviousState() || {
                x: 0,
                y: 0
              };
              camera.animate({
                x: cameraState.x + this.settings.inertiaRatio * (cameraState.x - previousCameraState.x),
                y: cameraState.y + this.settings.inertiaRatio * (cameraState.y - previousCameraState.y)
              }, {
                duration: this.settings.inertiaDuration,
                easing: "quadraticOut"
              });
            }
            this.hasMoved = false;
            this.isMoving = false;
            this.touchMode = 0;
            break;
        }
        this.emit("touchup", getTouchCoords(e, this.lastTouches, this.container));
        if (!e.touches.length) {
          var position = getPosition(this.lastTouches[0], this.container);
          var downPosition = this.startTouchesPositions[0];
          var dSquare = Math.pow(position.x - downPosition.x, 2) + Math.pow(position.y - downPosition.y, 2);
          if (!e.touches.length && dSquare < Math.pow(this.settings.tapMoveTolerance, 2)) {
            if (this.lastTap && Date.now() - this.lastTap.time < this.settings.doubleClickTimeout) {
              var touchCoords = getTouchCoords(e, this.lastTouches, this.container);
              this.emit("doubletap", touchCoords);
              this.lastTap = null;
              if (!touchCoords.sigmaDefaultPrevented) {
                var _camera = this.renderer.getCamera();
                var newRatio = _camera.getBoundedRatio(_camera.getState().ratio / this.settings.doubleClickZoomingRatio);
                _camera.animate(this.renderer.getViewportZoomedState(position, newRatio), {
                  easing: "quadraticInOut",
                  duration: this.settings.doubleClickZoomingDuration
                });
              }
            } else {
              var _touchCoords = getTouchCoords(e, this.lastTouches, this.container);
              this.emit("tap", _touchCoords);
              this.lastTap = {
                time: Date.now(),
                position: _touchCoords.touches[0] || _touchCoords.previousTouches[0]
              };
            }
          }
        }
        this.lastTouches = getTouchesArray(e.touches);
        this.startTouchesPositions = [];
      }
    }, {
      key: "handleMove",
      value: function handleMove(e) {
        var _this3 = this;
        if (!this.enabled || !this.startTouchesPositions.length) return;
        e.preventDefault();
        var touches = getTouchesArray(e.touches);
        var touchesPositions = touches.map(function(touch) {
          return getPosition(touch, _this3.container);
        });
        var lastTouches = this.lastTouches;
        this.lastTouches = touches;
        this.lastTouchesPositions = touchesPositions;
        var touchCoords = getTouchCoords(e, lastTouches, this.container);
        this.emit("touchmove", touchCoords);
        if (touchCoords.sigmaDefaultPrevented) return;
        this.hasMoved || (this.hasMoved = touchesPositions.some(function(position, idx) {
          var startPosition = _this3.startTouchesPositions[idx];
          return startPosition && (position.x !== startPosition.x || position.y !== startPosition.y);
        }));
        if (!this.hasMoved) {
          return;
        }
        this.isMoving = true;
        if (this.movingTimeout) clearTimeout(this.movingTimeout);
        this.movingTimeout = window.setTimeout(function() {
          _this3.isMoving = false;
        }, this.settings.dragTimeout);
        var camera = this.renderer.getCamera();
        var startCameraState = this.startCameraState;
        var padding = this.renderer.getSetting("stagePadding");
        switch (this.touchMode) {
          case 1: {
            var _this$renderer$viewpo = this.renderer.viewportToFramedGraph((this.startTouchesPositions || [])[0]), xStart = _this$renderer$viewpo.x, yStart = _this$renderer$viewpo.y;
            var _this$renderer$viewpo2 = this.renderer.viewportToFramedGraph(touchesPositions[0]), x = _this$renderer$viewpo2.x, y = _this$renderer$viewpo2.y;
            camera.setState({
              x: startCameraState.x + xStart - x,
              y: startCameraState.y + yStart - y
            });
            break;
          }
          case 2: {
            var newCameraState = {
              x: 0.5,
              y: 0.5,
              angle: 0,
              ratio: 1
            };
            var _touchesPositions$ = touchesPositions[0], x0 = _touchesPositions$.x, y0 = _touchesPositions$.y;
            var _touchesPositions$2 = touchesPositions[1], x1 = _touchesPositions$2.x, y1 = _touchesPositions$2.y;
            var angleDiff = Math.atan2(y1 - y0, x1 - x0) - this.startTouchesAngle;
            var ratioDiff = Math.hypot(y1 - y0, x1 - x0) / this.startTouchesDistance;
            var newRatio = camera.getBoundedRatio(startCameraState.ratio / ratioDiff);
            newCameraState.ratio = newRatio;
            newCameraState.angle = startCameraState.angle + angleDiff;
            var dimensions = this.getDimensions();
            var touchGraphPosition = this.renderer.viewportToFramedGraph((this.startTouchesPositions || [])[0], {
              cameraState: startCameraState
            });
            var smallestDimension = Math.min(dimensions.width, dimensions.height) - 2 * padding;
            var dx = smallestDimension / dimensions.width;
            var dy = smallestDimension / dimensions.height;
            var ratio = newRatio / smallestDimension;
            var _x = x0 - smallestDimension / 2 / dx;
            var _y = y0 - smallestDimension / 2 / dy;
            var _ref = [_x * Math.cos(-newCameraState.angle) - _y * Math.sin(-newCameraState.angle), _y * Math.cos(-newCameraState.angle) + _x * Math.sin(-newCameraState.angle)];
            _x = _ref[0];
            _y = _ref[1];
            newCameraState.x = touchGraphPosition.x - _x * ratio;
            newCameraState.y = touchGraphPosition.y + _y * ratio;
            camera.setState(newCameraState);
            break;
          }
        }
      }
    }, {
      key: "setSettings",
      value: function setSettings(settings) {
        this.settings = settings;
      }
    }]);
  })(Captor);
  function _arrayWithoutHoles(r) {
    if (Array.isArray(r)) return _arrayLikeToArray(r);
  }
  function _iterableToArray(r) {
    if ("undefined" != typeof Symbol && null != r[Symbol.iterator] || null != r["@@iterator"]) return Array.from(r);
  }
  function _nonIterableSpread() {
    throw new TypeError("Invalid attempt to spread non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method.");
  }
  function _toConsumableArray(r) {
    return _arrayWithoutHoles(r) || _iterableToArray(r) || _unsupportedIterableToArray(r) || _nonIterableSpread();
  }
  function _objectWithoutPropertiesLoose(r, e) {
    if (null == r) return {};
    var t = {};
    for (var n in r) if ({}.hasOwnProperty.call(r, n)) {
      if (-1 !== e.indexOf(n)) continue;
      t[n] = r[n];
    }
    return t;
  }
  function _objectWithoutProperties(e, t) {
    if (null == e) return {};
    var o, r, i = _objectWithoutPropertiesLoose(e, t);
    if (Object.getOwnPropertySymbols) {
      var n = Object.getOwnPropertySymbols(e);
      for (r = 0; r < n.length; r++) o = n[r], -1 === t.indexOf(o) && {}.propertyIsEnumerable.call(e, o) && (i[o] = e[o]);
    }
    return i;
  }
  var LabelCandidate = /* @__PURE__ */ (function() {
    function LabelCandidate2(key, size) {
      _classCallCheck(this, LabelCandidate2);
      this.key = key;
      this.size = size;
    }
    return _createClass(LabelCandidate2, null, [{
      key: "compare",
      value: function compare(first, second) {
        if (first.size > second.size) return -1;
        if (first.size < second.size) return 1;
        if (first.key > second.key) return 1;
        return -1;
      }
    }]);
  })();
  var LabelGrid = /* @__PURE__ */ (function() {
    function LabelGrid2() {
      _classCallCheck(this, LabelGrid2);
      _defineProperty(this, "width", 0);
      _defineProperty(this, "height", 0);
      _defineProperty(this, "cellSize", 0);
      _defineProperty(this, "columns", 0);
      _defineProperty(this, "rows", 0);
      _defineProperty(this, "cells", {});
    }
    return _createClass(LabelGrid2, [{
      key: "resizeAndClear",
      value: function resizeAndClear(dimensions, cellSize) {
        this.width = dimensions.width;
        this.height = dimensions.height;
        this.cellSize = cellSize;
        this.columns = Math.ceil(dimensions.width / cellSize);
        this.rows = Math.ceil(dimensions.height / cellSize);
        this.cells = {};
      }
    }, {
      key: "getIndex",
      value: function getIndex(pos) {
        var xIndex = Math.floor(pos.x / this.cellSize);
        var yIndex = Math.floor(pos.y / this.cellSize);
        return yIndex * this.columns + xIndex;
      }
    }, {
      key: "add",
      value: function add(key, size, pos) {
        var candidate = new LabelCandidate(key, size);
        var index = this.getIndex(pos);
        var cell = this.cells[index];
        if (!cell) {
          cell = [];
          this.cells[index] = cell;
        }
        cell.push(candidate);
      }
    }, {
      key: "organize",
      value: function organize() {
        for (var k in this.cells) {
          var cell = this.cells[k];
          cell.sort(LabelCandidate.compare);
        }
      }
    }, {
      key: "getLabelsToDisplay",
      value: function getLabelsToDisplay(ratio, density) {
        var cellArea = this.cellSize * this.cellSize;
        var scaledCellArea = cellArea / ratio / ratio;
        var scaledDensity = scaledCellArea * density / cellArea;
        var labelsToDisplayPerCell = Math.ceil(scaledDensity);
        var labels = [];
        for (var k in this.cells) {
          var cell = this.cells[k];
          for (var i = 0; i < Math.min(labelsToDisplayPerCell, cell.length); i++) {
            labels.push(cell[i].key);
          }
        }
        return labels;
      }
    }]);
  })();
  function edgeLabelsToDisplayFromNodes(params) {
    var graph = params.graph, hoveredNode = params.hoveredNode, highlightedNodes = params.highlightedNodes, displayedNodeLabels = params.displayedNodeLabels;
    var worthyEdges = [];
    graph.forEachEdge(function(edge, _, source, target) {
      if (source === hoveredNode || target === hoveredNode || highlightedNodes.has(source) || highlightedNodes.has(target) || displayedNodeLabels.has(source) && displayedNodeLabels.has(target)) {
        worthyEdges.push(edge);
      }
    });
    return worthyEdges;
  }
  var X_LABEL_MARGIN = 150;
  var Y_LABEL_MARGIN = 50;
  var hasOwnProperty = Object.prototype.hasOwnProperty;
  function applyNodeDefaults(settings, key, data) {
    if (!hasOwnProperty.call(data, "x") || !hasOwnProperty.call(data, "y")) throw new Error('Sigma: could not find a valid position (x, y) for node "'.concat(key, '". All your nodes must have a number "x" and "y". Maybe your forgot to apply a layout or your "nodeReducer" is not returning the correct data?'));
    if (!data.color) data.color = settings.defaultNodeColor;
    if (!data.label && data.label !== "") data.label = null;
    if (data.label !== void 0 && data.label !== null) data.label = "" + data.label;
    else data.label = null;
    if (!data.size) data.size = 2;
    if (!hasOwnProperty.call(data, "hidden")) data.hidden = false;
    if (!hasOwnProperty.call(data, "highlighted")) data.highlighted = false;
    if (!hasOwnProperty.call(data, "forceLabel")) data.forceLabel = false;
    if (!data.type || data.type === "") data.type = settings.defaultNodeType;
    if (!data.zIndex) data.zIndex = 0;
    return data;
  }
  function applyEdgeDefaults(settings, _key, data) {
    if (!data.color) data.color = settings.defaultEdgeColor;
    if (!data.label) data.label = "";
    if (!data.size) data.size = 0.5;
    if (!hasOwnProperty.call(data, "hidden")) data.hidden = false;
    if (!hasOwnProperty.call(data, "forceLabel")) data.forceLabel = false;
    if (!data.type || data.type === "") data.type = settings.defaultEdgeType;
    if (!data.zIndex) data.zIndex = 0;
    return data;
  }
  var Sigma$1 = /* @__PURE__ */ (function(_TypedEventEmitter) {
    function Sigma2(graph, container) {
      var _this;
      var settings = arguments.length > 2 && arguments[2] !== void 0 ? arguments[2] : {};
      _classCallCheck(this, Sigma2);
      _this = _callSuper(this, Sigma2);
      _defineProperty(_this, "elements", {});
      _defineProperty(_this, "canvasContexts", {});
      _defineProperty(_this, "webGLContexts", {});
      _defineProperty(_this, "pickingLayers", /* @__PURE__ */ new Set());
      _defineProperty(_this, "textures", {});
      _defineProperty(_this, "frameBuffers", {});
      _defineProperty(_this, "activeListeners", {});
      _defineProperty(_this, "labelGrid", new LabelGrid());
      _defineProperty(_this, "nodeDataCache", {});
      _defineProperty(_this, "edgeDataCache", {});
      _defineProperty(_this, "nodeProgramIndex", {});
      _defineProperty(_this, "edgeProgramIndex", {});
      _defineProperty(_this, "nodesWithForcedLabels", /* @__PURE__ */ new Set());
      _defineProperty(_this, "edgesWithForcedLabels", /* @__PURE__ */ new Set());
      _defineProperty(_this, "nodeExtent", {
        x: [0, 1],
        y: [0, 1]
      });
      _defineProperty(_this, "nodeZExtent", [Infinity, -Infinity]);
      _defineProperty(_this, "edgeZExtent", [Infinity, -Infinity]);
      _defineProperty(_this, "matrix", identity());
      _defineProperty(_this, "invMatrix", identity());
      _defineProperty(_this, "correctionRatio", 1);
      _defineProperty(_this, "customBBox", null);
      _defineProperty(_this, "normalizationFunction", createNormalizationFunction({
        x: [0, 1],
        y: [0, 1]
      }));
      _defineProperty(_this, "graphToViewportRatio", 1);
      _defineProperty(_this, "itemIDsIndex", {});
      _defineProperty(_this, "nodeIndices", {});
      _defineProperty(_this, "edgeIndices", {});
      _defineProperty(_this, "width", 0);
      _defineProperty(_this, "height", 0);
      _defineProperty(_this, "pixelRatio", getPixelRatio());
      _defineProperty(_this, "pickingDownSizingRatio", 2 * _this.pixelRatio);
      _defineProperty(_this, "displayedNodeLabels", /* @__PURE__ */ new Set());
      _defineProperty(_this, "displayedEdgeLabels", /* @__PURE__ */ new Set());
      _defineProperty(_this, "highlightedNodes", /* @__PURE__ */ new Set());
      _defineProperty(_this, "hoveredNode", null);
      _defineProperty(_this, "hoveredEdge", null);
      _defineProperty(_this, "renderFrame", null);
      _defineProperty(_this, "renderHighlightedNodesFrame", null);
      _defineProperty(_this, "needToProcess", false);
      _defineProperty(_this, "checkEdgesEventsFrame", null);
      _defineProperty(_this, "nodePrograms", {});
      _defineProperty(_this, "nodeHoverPrograms", {});
      _defineProperty(_this, "edgePrograms", {});
      _this.settings = resolveSettings(settings);
      validateSettings(_this.settings);
      validateGraph(graph);
      if (!(container instanceof HTMLElement)) throw new Error("Sigma: container should be an html element.");
      _this.graph = graph;
      _this.container = container;
      _this.createWebGLContext("edges", {
        picking: settings.enableEdgeEvents
      });
      _this.createCanvasContext("edgeLabels");
      _this.createWebGLContext("nodes", {
        picking: true
      });
      _this.createCanvasContext("labels");
      _this.createCanvasContext("hovers");
      _this.createWebGLContext("hoverNodes");
      _this.createCanvasContext("mouse", {
        style: {
          touchAction: "none",
          userSelect: "none"
        }
      });
      _this.resize();
      for (var type in _this.settings.nodeProgramClasses) {
        _this.registerNodeProgram(type, _this.settings.nodeProgramClasses[type], _this.settings.nodeHoverProgramClasses[type]);
      }
      for (var _type in _this.settings.edgeProgramClasses) {
        _this.registerEdgeProgram(_type, _this.settings.edgeProgramClasses[_type]);
      }
      _this.camera = new Camera();
      _this.bindCameraHandlers();
      _this.mouseCaptor = new MouseCaptor(_this.elements.mouse, _this);
      _this.mouseCaptor.setSettings(_this.settings);
      _this.touchCaptor = new TouchCaptor(_this.elements.mouse, _this);
      _this.touchCaptor.setSettings(_this.settings);
      _this.bindEventHandlers();
      _this.bindGraphHandlers();
      _this.handleSettingsUpdate();
      _this.refresh();
      return _this;
    }
    _inherits(Sigma2, _TypedEventEmitter);
    return _createClass(Sigma2, [{
      key: "registerNodeProgram",
      value: function registerNodeProgram(key, NodeProgramClass, NodeHoverProgram) {
        if (this.nodePrograms[key]) this.nodePrograms[key].kill();
        if (this.nodeHoverPrograms[key]) this.nodeHoverPrograms[key].kill();
        this.nodePrograms[key] = new NodeProgramClass(this.webGLContexts.nodes, this.frameBuffers.nodes, this);
        this.nodeHoverPrograms[key] = new (NodeHoverProgram || NodeProgramClass)(this.webGLContexts.hoverNodes, null, this);
        return this;
      }
      /**
       * Internal function used to register an edge program
       *
       * @param  {string}          key              - The program's key, matching the related edges "type" values.
       * @param  {EdgeProgramType} EdgeProgramClass - An edges program class.
       * @return {Sigma}
       */
    }, {
      key: "registerEdgeProgram",
      value: function registerEdgeProgram(key, EdgeProgramClass) {
        if (this.edgePrograms[key]) this.edgePrograms[key].kill();
        this.edgePrograms[key] = new EdgeProgramClass(this.webGLContexts.edges, this.frameBuffers.edges, this);
        return this;
      }
      /**
       * Internal function used to unregister a node program
       *
       * @param  {string} key - The program's key, matching the related nodes "type" values.
       * @return {Sigma}
       */
    }, {
      key: "unregisterNodeProgram",
      value: function unregisterNodeProgram(key) {
        if (this.nodePrograms[key]) {
          var _this$nodePrograms = this.nodePrograms, program = _this$nodePrograms[key], programs = _objectWithoutProperties(_this$nodePrograms, [key].map(_toPropertyKey));
          program.kill();
          this.nodePrograms = programs;
        }
        if (this.nodeHoverPrograms[key]) {
          var _this$nodeHoverProgra = this.nodeHoverPrograms, _program = _this$nodeHoverProgra[key], _programs = _objectWithoutProperties(_this$nodeHoverProgra, [key].map(_toPropertyKey));
          _program.kill();
          this.nodePrograms = _programs;
        }
        return this;
      }
      /**
       * Internal function used to unregister an edge program
       *
       * @param  {string} key - The program's key, matching the related edges "type" values.
       * @return {Sigma}
       */
    }, {
      key: "unregisterEdgeProgram",
      value: function unregisterEdgeProgram(key) {
        if (this.edgePrograms[key]) {
          var _this$edgePrograms = this.edgePrograms, program = _this$edgePrograms[key], programs = _objectWithoutProperties(_this$edgePrograms, [key].map(_toPropertyKey));
          program.kill();
          this.edgePrograms = programs;
        }
        return this;
      }
      /**
       * Method (re)binding WebGL texture (for picking).
       *
       * @return {Sigma}
       */
    }, {
      key: "resetWebGLTexture",
      value: function resetWebGLTexture(id) {
        var gl = this.webGLContexts[id];
        var frameBuffer = this.frameBuffers[id];
        var currentTexture = this.textures[id];
        if (currentTexture) gl.deleteTexture(currentTexture);
        var pickingTexture = gl.createTexture();
        gl.bindFramebuffer(gl.FRAMEBUFFER, frameBuffer);
        gl.bindTexture(gl.TEXTURE_2D, pickingTexture);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, this.width, this.height, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, pickingTexture, 0);
        this.textures[id] = pickingTexture;
        return this;
      }
      /**
       * Method binding camera handlers.
       *
       * @return {Sigma}
       */
    }, {
      key: "bindCameraHandlers",
      value: function bindCameraHandlers() {
        var _this2 = this;
        this.activeListeners.camera = function() {
          _this2.scheduleRender();
        };
        this.camera.on("updated", this.activeListeners.camera);
        return this;
      }
      /**
       * Method unbinding camera handlers.
       *
       * @return {Sigma}
       */
    }, {
      key: "unbindCameraHandlers",
      value: function unbindCameraHandlers() {
        this.camera.removeListener("updated", this.activeListeners.camera);
        return this;
      }
      /**
       * Method that returns the closest node to a given position.
       */
    }, {
      key: "getNodeAtPosition",
      value: function getNodeAtPosition(position) {
        var x = position.x, y = position.y;
        var color = getPixelColor(this.webGLContexts.nodes, this.frameBuffers.nodes, x, y, this.pixelRatio, this.pickingDownSizingRatio);
        var index = colorToIndex.apply(void 0, _toConsumableArray(color));
        var itemAt = this.itemIDsIndex[index];
        return itemAt && itemAt.type === "node" ? itemAt.id : null;
      }
      /**
       * Method binding event handlers.
       *
       * @return {Sigma}
       */
    }, {
      key: "bindEventHandlers",
      value: function bindEventHandlers() {
        var _this3 = this;
        this.activeListeners.handleResize = function() {
          _this3.scheduleRefresh();
        };
        window.addEventListener("resize", this.activeListeners.handleResize);
        this.activeListeners.handleMove = function(e) {
          var event = cleanMouseCoords(e);
          var baseEvent = {
            event,
            preventSigmaDefault: function preventSigmaDefault() {
              event.preventSigmaDefault();
            }
          };
          var nodeToHover = _this3.getNodeAtPosition(event);
          if (nodeToHover && _this3.hoveredNode !== nodeToHover && !_this3.nodeDataCache[nodeToHover].hidden) {
            if (_this3.hoveredNode) _this3.emit("leaveNode", _objectSpread2(_objectSpread2({}, baseEvent), {}, {
              node: _this3.hoveredNode
            }));
            _this3.hoveredNode = nodeToHover;
            _this3.emit("enterNode", _objectSpread2(_objectSpread2({}, baseEvent), {}, {
              node: nodeToHover
            }));
            _this3.scheduleHighlightedNodesRender();
            return;
          }
          if (_this3.hoveredNode) {
            if (_this3.getNodeAtPosition(event) !== _this3.hoveredNode) {
              var node = _this3.hoveredNode;
              _this3.hoveredNode = null;
              _this3.emit("leaveNode", _objectSpread2(_objectSpread2({}, baseEvent), {}, {
                node
              }));
              _this3.scheduleHighlightedNodesRender();
              return;
            }
          }
          if (_this3.settings.enableEdgeEvents) {
            var edgeToHover = _this3.hoveredNode ? null : _this3.getEdgeAtPoint(baseEvent.event.x, baseEvent.event.y);
            if (edgeToHover !== _this3.hoveredEdge) {
              if (_this3.hoveredEdge) _this3.emit("leaveEdge", _objectSpread2(_objectSpread2({}, baseEvent), {}, {
                edge: _this3.hoveredEdge
              }));
              if (edgeToHover) _this3.emit("enterEdge", _objectSpread2(_objectSpread2({}, baseEvent), {}, {
                edge: edgeToHover
              }));
              _this3.hoveredEdge = edgeToHover;
            }
          }
        };
        this.activeListeners.handleMoveBody = function(e) {
          var event = cleanMouseCoords(e);
          _this3.emit("moveBody", {
            event,
            preventSigmaDefault: function preventSigmaDefault() {
              event.preventSigmaDefault();
            }
          });
        };
        this.activeListeners.handleLeave = function(e) {
          var event = cleanMouseCoords(e);
          var baseEvent = {
            event,
            preventSigmaDefault: function preventSigmaDefault() {
              event.preventSigmaDefault();
            }
          };
          if (_this3.hoveredNode) {
            _this3.emit("leaveNode", _objectSpread2(_objectSpread2({}, baseEvent), {}, {
              node: _this3.hoveredNode
            }));
            _this3.scheduleHighlightedNodesRender();
          }
          if (_this3.settings.enableEdgeEvents && _this3.hoveredEdge) {
            _this3.emit("leaveEdge", _objectSpread2(_objectSpread2({}, baseEvent), {}, {
              edge: _this3.hoveredEdge
            }));
            _this3.scheduleHighlightedNodesRender();
          }
          _this3.emit("leaveStage", _objectSpread2({}, baseEvent));
        };
        this.activeListeners.handleEnter = function(e) {
          var event = cleanMouseCoords(e);
          var baseEvent = {
            event,
            preventSigmaDefault: function preventSigmaDefault() {
              event.preventSigmaDefault();
            }
          };
          _this3.emit("enterStage", _objectSpread2({}, baseEvent));
        };
        var createInteractionListener = function createInteractionListener2(eventType) {
          return function(e) {
            var event = cleanMouseCoords(e);
            var baseEvent = {
              event,
              preventSigmaDefault: function preventSigmaDefault() {
                event.preventSigmaDefault();
              }
            };
            var nodeAtPosition = _this3.getNodeAtPosition(event);
            if (nodeAtPosition) return _this3.emit("".concat(eventType, "Node"), _objectSpread2(_objectSpread2({}, baseEvent), {}, {
              node: nodeAtPosition
            }));
            if (_this3.settings.enableEdgeEvents) {
              var edge = _this3.getEdgeAtPoint(event.x, event.y);
              if (edge) return _this3.emit("".concat(eventType, "Edge"), _objectSpread2(_objectSpread2({}, baseEvent), {}, {
                edge
              }));
            }
            return _this3.emit("".concat(eventType, "Stage"), baseEvent);
          };
        };
        this.activeListeners.handleClick = createInteractionListener("click");
        this.activeListeners.handleRightClick = createInteractionListener("rightClick");
        this.activeListeners.handleDoubleClick = createInteractionListener("doubleClick");
        this.activeListeners.handleWheel = createInteractionListener("wheel");
        this.activeListeners.handleDown = createInteractionListener("down");
        this.activeListeners.handleUp = createInteractionListener("up");
        this.mouseCaptor.on("mousemove", this.activeListeners.handleMove);
        this.mouseCaptor.on("mousemovebody", this.activeListeners.handleMoveBody);
        this.mouseCaptor.on("click", this.activeListeners.handleClick);
        this.mouseCaptor.on("rightClick", this.activeListeners.handleRightClick);
        this.mouseCaptor.on("doubleClick", this.activeListeners.handleDoubleClick);
        this.mouseCaptor.on("wheel", this.activeListeners.handleWheel);
        this.mouseCaptor.on("mousedown", this.activeListeners.handleDown);
        this.mouseCaptor.on("mouseup", this.activeListeners.handleUp);
        this.mouseCaptor.on("mouseleave", this.activeListeners.handleLeave);
        this.mouseCaptor.on("mouseenter", this.activeListeners.handleEnter);
        this.touchCaptor.on("touchdown", this.activeListeners.handleDown);
        this.touchCaptor.on("touchdown", this.activeListeners.handleMove);
        this.touchCaptor.on("touchup", this.activeListeners.handleUp);
        this.touchCaptor.on("touchmove", this.activeListeners.handleMove);
        this.touchCaptor.on("tap", this.activeListeners.handleClick);
        this.touchCaptor.on("doubletap", this.activeListeners.handleDoubleClick);
        this.touchCaptor.on("touchmove", this.activeListeners.handleMoveBody);
        return this;
      }
      /**
       * Method binding graph handlers
       *
       * @return {Sigma}
       */
    }, {
      key: "bindGraphHandlers",
      value: function bindGraphHandlers() {
        var _this4 = this;
        var graph = this.graph;
        var LAYOUT_IMPACTING_FIELDS = /* @__PURE__ */ new Set(["x", "y", "zIndex", "type"]);
        this.activeListeners.eachNodeAttributesUpdatedGraphUpdate = function(e) {
          var _e$hints;
          var updatedFields = (_e$hints = e.hints) === null || _e$hints === void 0 ? void 0 : _e$hints.attributes;
          _this4.graph.forEachNode(function(node) {
            return _this4.updateNode(node);
          });
          var layoutChanged = !updatedFields || updatedFields.some(function(f) {
            return LAYOUT_IMPACTING_FIELDS.has(f);
          });
          _this4.refresh({
            partialGraph: {
              nodes: graph.nodes()
            },
            skipIndexation: !layoutChanged,
            schedule: true
          });
        };
        this.activeListeners.eachEdgeAttributesUpdatedGraphUpdate = function(e) {
          var _e$hints2;
          var updatedFields = (_e$hints2 = e.hints) === null || _e$hints2 === void 0 ? void 0 : _e$hints2.attributes;
          _this4.graph.forEachEdge(function(edge) {
            return _this4.updateEdge(edge);
          });
          var layoutChanged = updatedFields && ["zIndex", "type"].some(function(f) {
            return updatedFields === null || updatedFields === void 0 ? void 0 : updatedFields.includes(f);
          });
          _this4.refresh({
            partialGraph: {
              edges: graph.edges()
            },
            skipIndexation: !layoutChanged,
            schedule: true
          });
        };
        this.activeListeners.addNodeGraphUpdate = function(payload) {
          var node = payload.key;
          _this4.addNode(node);
          _this4.refresh({
            partialGraph: {
              nodes: [node]
            },
            skipIndexation: false,
            schedule: true
          });
        };
        this.activeListeners.updateNodeGraphUpdate = function(payload) {
          var node = payload.key;
          _this4.refresh({
            partialGraph: {
              nodes: [node]
            },
            skipIndexation: false,
            schedule: true
          });
        };
        this.activeListeners.dropNodeGraphUpdate = function(payload) {
          var node = payload.key;
          _this4.removeNode(node);
          _this4.refresh({
            schedule: true
          });
        };
        this.activeListeners.addEdgeGraphUpdate = function(payload) {
          var edge = payload.key;
          _this4.addEdge(edge);
          _this4.refresh({
            partialGraph: {
              edges: [edge]
            },
            schedule: true
          });
        };
        this.activeListeners.updateEdgeGraphUpdate = function(payload) {
          var edge = payload.key;
          _this4.refresh({
            partialGraph: {
              edges: [edge]
            },
            skipIndexation: false,
            schedule: true
          });
        };
        this.activeListeners.dropEdgeGraphUpdate = function(payload) {
          var edge = payload.key;
          _this4.removeEdge(edge);
          _this4.refresh({
            schedule: true
          });
        };
        this.activeListeners.clearEdgesGraphUpdate = function() {
          _this4.clearEdgeState();
          _this4.clearEdgeIndices();
          _this4.refresh({
            schedule: true
          });
        };
        this.activeListeners.clearGraphUpdate = function() {
          _this4.clearEdgeState();
          _this4.clearNodeState();
          _this4.clearEdgeIndices();
          _this4.clearNodeIndices();
          _this4.refresh({
            schedule: true
          });
        };
        graph.on("nodeAdded", this.activeListeners.addNodeGraphUpdate);
        graph.on("nodeDropped", this.activeListeners.dropNodeGraphUpdate);
        graph.on("nodeAttributesUpdated", this.activeListeners.updateNodeGraphUpdate);
        graph.on("eachNodeAttributesUpdated", this.activeListeners.eachNodeAttributesUpdatedGraphUpdate);
        graph.on("edgeAdded", this.activeListeners.addEdgeGraphUpdate);
        graph.on("edgeDropped", this.activeListeners.dropEdgeGraphUpdate);
        graph.on("edgeAttributesUpdated", this.activeListeners.updateEdgeGraphUpdate);
        graph.on("eachEdgeAttributesUpdated", this.activeListeners.eachEdgeAttributesUpdatedGraphUpdate);
        graph.on("edgesCleared", this.activeListeners.clearEdgesGraphUpdate);
        graph.on("cleared", this.activeListeners.clearGraphUpdate);
        return this;
      }
      /**
       * Method used to unbind handlers from the graph.
       *
       * @return {undefined}
       */
    }, {
      key: "unbindGraphHandlers",
      value: function unbindGraphHandlers() {
        var graph = this.graph;
        graph.removeListener("nodeAdded", this.activeListeners.addNodeGraphUpdate);
        graph.removeListener("nodeDropped", this.activeListeners.dropNodeGraphUpdate);
        graph.removeListener("nodeAttributesUpdated", this.activeListeners.updateNodeGraphUpdate);
        graph.removeListener("eachNodeAttributesUpdated", this.activeListeners.eachNodeAttributesUpdatedGraphUpdate);
        graph.removeListener("edgeAdded", this.activeListeners.addEdgeGraphUpdate);
        graph.removeListener("edgeDropped", this.activeListeners.dropEdgeGraphUpdate);
        graph.removeListener("edgeAttributesUpdated", this.activeListeners.updateEdgeGraphUpdate);
        graph.removeListener("eachEdgeAttributesUpdated", this.activeListeners.eachEdgeAttributesUpdatedGraphUpdate);
        graph.removeListener("edgesCleared", this.activeListeners.clearEdgesGraphUpdate);
        graph.removeListener("cleared", this.activeListeners.clearGraphUpdate);
      }
      /**
       * Method looking for an edge colliding with a given point at (x, y). Returns
       * the key of the edge if any, or null else.
       */
    }, {
      key: "getEdgeAtPoint",
      value: function getEdgeAtPoint(x, y) {
        var color = getPixelColor(this.webGLContexts.edges, this.frameBuffers.edges, x, y, this.pixelRatio, this.pickingDownSizingRatio);
        var index = colorToIndex.apply(void 0, _toConsumableArray(color));
        var itemAt = this.itemIDsIndex[index];
        return itemAt && itemAt.type === "edge" ? itemAt.id : null;
      }
      /**
       * Method used to process the whole graph's data.
       *  - extent
       *  - normalizationFunction
       *  - compute node's coordinate
       *  - labelgrid
       *  - program data allocation
       * @return {Sigma}
       */
    }, {
      key: "process",
      value: function process() {
        var _this5 = this;
        this.emit("beforeProcess");
        var graph = this.graph;
        var settings = this.settings;
        var dimensions = this.getDimensions();
        this.nodeExtent = graphExtent(this.graph);
        if (!this.settings.autoRescale) {
          var width = dimensions.width, height = dimensions.height;
          var _this$nodeExtent = this.nodeExtent, x = _this$nodeExtent.x, y = _this$nodeExtent.y;
          this.nodeExtent = {
            x: [(x[0] + x[1]) / 2 - width / 2, (x[0] + x[1]) / 2 + width / 2],
            y: [(y[0] + y[1]) / 2 - height / 2, (y[0] + y[1]) / 2 + height / 2]
          };
        }
        this.normalizationFunction = createNormalizationFunction(this.customBBox || this.nodeExtent);
        var nullCamera = new Camera();
        var nullCameraMatrix = matrixFromCamera(nullCamera.getState(), dimensions, this.getGraphDimensions(), this.getStagePadding());
        this.labelGrid.resizeAndClear(dimensions, settings.labelGridCellSize);
        var nodesPerPrograms = {};
        var nodeIndices = {};
        var edgeIndices = {};
        var itemIDsIndex = {};
        var incrID = 1;
        var nodes = graph.nodes();
        for (var i = 0, l = nodes.length; i < l; i++) {
          var node = nodes[i];
          var data = this.nodeDataCache[node];
          var attrs = graph.getNodeAttributes(node);
          data.x = attrs.x;
          data.y = attrs.y;
          this.normalizationFunction.applyTo(data);
          if (typeof data.label === "string" && !data.hidden) this.labelGrid.add(node, data.size, this.framedGraphToViewport(data, {
            matrix: nullCameraMatrix
          }));
          nodesPerPrograms[data.type] = (nodesPerPrograms[data.type] || 0) + 1;
        }
        this.labelGrid.organize();
        for (var type in this.nodePrograms) {
          if (!hasOwnProperty.call(this.nodePrograms, type)) {
            throw new Error('Sigma: could not find a suitable program for node type "'.concat(type, '"!'));
          }
          this.nodePrograms[type].reallocate(nodesPerPrograms[type] || 0);
          nodesPerPrograms[type] = 0;
        }
        if (this.settings.zIndex && this.nodeZExtent[0] !== this.nodeZExtent[1]) nodes = zIndexOrdering(this.nodeZExtent, function(node2) {
          return _this5.nodeDataCache[node2].zIndex;
        }, nodes);
        for (var _i = 0, _l = nodes.length; _i < _l; _i++) {
          var _node = nodes[_i];
          nodeIndices[_node] = incrID;
          itemIDsIndex[nodeIndices[_node]] = {
            type: "node",
            id: _node
          };
          incrID++;
          var _data = this.nodeDataCache[_node];
          this.addNodeToProgram(_node, nodeIndices[_node], nodesPerPrograms[_data.type]++);
        }
        var edgesPerPrograms = {};
        var edges = graph.edges();
        for (var _i2 = 0, _l2 = edges.length; _i2 < _l2; _i2++) {
          var edge = edges[_i2];
          var _data2 = this.edgeDataCache[edge];
          edgesPerPrograms[_data2.type] = (edgesPerPrograms[_data2.type] || 0) + 1;
        }
        if (this.settings.zIndex && this.edgeZExtent[0] !== this.edgeZExtent[1]) edges = zIndexOrdering(this.edgeZExtent, function(edge2) {
          return _this5.edgeDataCache[edge2].zIndex;
        }, edges);
        for (var _type2 in this.edgePrograms) {
          if (!hasOwnProperty.call(this.edgePrograms, _type2)) {
            throw new Error('Sigma: could not find a suitable program for edge type "'.concat(_type2, '"!'));
          }
          this.edgePrograms[_type2].reallocate(edgesPerPrograms[_type2] || 0);
          edgesPerPrograms[_type2] = 0;
        }
        for (var _i3 = 0, _l3 = edges.length; _i3 < _l3; _i3++) {
          var _edge = edges[_i3];
          edgeIndices[_edge] = incrID;
          itemIDsIndex[edgeIndices[_edge]] = {
            type: "edge",
            id: _edge
          };
          incrID++;
          var _data3 = this.edgeDataCache[_edge];
          this.addEdgeToProgram(_edge, edgeIndices[_edge], edgesPerPrograms[_data3.type]++);
        }
        this.itemIDsIndex = itemIDsIndex;
        this.nodeIndices = nodeIndices;
        this.edgeIndices = edgeIndices;
        this.emit("afterProcess");
        return this;
      }
      /**
       * Method that backports potential settings updates where it's needed.
       * @private
       */
    }, {
      key: "handleSettingsUpdate",
      value: function handleSettingsUpdate(oldSettings) {
        var _this6 = this;
        var settings = this.settings;
        this.camera.minRatio = settings.minCameraRatio;
        this.camera.maxRatio = settings.maxCameraRatio;
        this.camera.enabledZooming = settings.enableCameraZooming;
        this.camera.enabledPanning = settings.enableCameraPanning;
        this.camera.enabledRotation = settings.enableCameraRotation;
        if (settings.cameraPanBoundaries) {
          this.camera.clean = function(state) {
            return _this6.cleanCameraState(state, settings.cameraPanBoundaries && _typeof(settings.cameraPanBoundaries) === "object" ? settings.cameraPanBoundaries : {});
          };
        } else {
          this.camera.clean = null;
        }
        this.camera.setState(this.camera.validateState(this.camera.getState()));
        if (oldSettings) {
          if (oldSettings.edgeProgramClasses !== settings.edgeProgramClasses) {
            for (var type in settings.edgeProgramClasses) {
              if (settings.edgeProgramClasses[type] !== oldSettings.edgeProgramClasses[type]) {
                this.registerEdgeProgram(type, settings.edgeProgramClasses[type]);
              }
            }
            for (var _type3 in oldSettings.edgeProgramClasses) {
              if (!settings.edgeProgramClasses[_type3]) this.unregisterEdgeProgram(_type3);
            }
          }
          if (oldSettings.nodeProgramClasses !== settings.nodeProgramClasses || oldSettings.nodeHoverProgramClasses !== settings.nodeHoverProgramClasses) {
            for (var _type4 in settings.nodeProgramClasses) {
              if (settings.nodeProgramClasses[_type4] !== oldSettings.nodeProgramClasses[_type4] || settings.nodeHoverProgramClasses[_type4] !== oldSettings.nodeHoverProgramClasses[_type4]) {
                this.registerNodeProgram(_type4, settings.nodeProgramClasses[_type4], settings.nodeHoverProgramClasses[_type4]);
              }
            }
            for (var _type5 in oldSettings.nodeProgramClasses) {
              if (!settings.nodeProgramClasses[_type5]) this.unregisterNodeProgram(_type5);
            }
          }
        }
        this.mouseCaptor.setSettings(this.settings);
        this.touchCaptor.setSettings(this.settings);
        return this;
      }
    }, {
      key: "cleanCameraState",
      value: function cleanCameraState(state) {
        var _ref = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : {}, _ref$tolerance = _ref.tolerance, tolerance = _ref$tolerance === void 0 ? 0 : _ref$tolerance, boundaries = _ref.boundaries;
        var newState = _objectSpread2({}, state);
        var _ref2 = boundaries || this.nodeExtent, _ref2$x = _slicedToArray(_ref2.x, 2), xMinGraph = _ref2$x[0], xMaxGraph = _ref2$x[1], _ref2$y = _slicedToArray(_ref2.y, 2), yMinGraph = _ref2$y[0], yMaxGraph = _ref2$y[1];
        var corners = [this.graphToViewport({
          x: xMinGraph,
          y: yMinGraph
        }, {
          cameraState: state
        }), this.graphToViewport({
          x: xMaxGraph,
          y: yMinGraph
        }, {
          cameraState: state
        }), this.graphToViewport({
          x: xMinGraph,
          y: yMaxGraph
        }, {
          cameraState: state
        }), this.graphToViewport({
          x: xMaxGraph,
          y: yMaxGraph
        }, {
          cameraState: state
        })];
        var xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
        corners.forEach(function(_ref3) {
          var x = _ref3.x, y = _ref3.y;
          xMin = Math.min(xMin, x);
          xMax = Math.max(xMax, x);
          yMin = Math.min(yMin, y);
          yMax = Math.max(yMax, y);
        });
        var graphWidth = xMax - xMin;
        var graphHeight = yMax - yMin;
        var _this$getDimensions = this.getDimensions(), width = _this$getDimensions.width, height = _this$getDimensions.height;
        var dx = 0;
        var dy = 0;
        if (graphWidth >= width) {
          if (xMax < width - tolerance) dx = xMax - (width - tolerance);
          else if (xMin > tolerance) dx = xMin - tolerance;
        } else {
          if (xMax > width + tolerance) dx = xMax - (width + tolerance);
          else if (xMin < -tolerance) dx = xMin + tolerance;
        }
        if (graphHeight >= height) {
          if (yMax < height - tolerance) dy = yMax - (height - tolerance);
          else if (yMin > tolerance) dy = yMin - tolerance;
        } else {
          if (yMax > height + tolerance) dy = yMax - (height + tolerance);
          else if (yMin < -tolerance) dy = yMin + tolerance;
        }
        if (dx || dy) {
          var origin = this.viewportToFramedGraph({
            x: 0,
            y: 0
          }, {
            cameraState: state
          });
          var delta = this.viewportToFramedGraph({
            x: dx,
            y: dy
          }, {
            cameraState: state
          });
          dx = delta.x - origin.x;
          dy = delta.y - origin.y;
          newState.x += dx;
          newState.y += dy;
        }
        return newState;
      }
      /**
       * Method used to render labels.
       *
       * @return {Sigma}
       */
    }, {
      key: "renderLabels",
      value: function renderLabels() {
        if (!this.settings.renderLabels) return this;
        var cameraState = this.camera.getState();
        var labelsToDisplay = this.labelGrid.getLabelsToDisplay(cameraState.ratio, this.settings.labelDensity);
        extend(labelsToDisplay, this.nodesWithForcedLabels);
        this.displayedNodeLabels = /* @__PURE__ */ new Set();
        var context = this.canvasContexts.labels;
        for (var i = 0, l = labelsToDisplay.length; i < l; i++) {
          var node = labelsToDisplay[i];
          var data = this.nodeDataCache[node];
          if (this.displayedNodeLabels.has(node)) continue;
          if (data.hidden) continue;
          var _this$framedGraphToVi = this.framedGraphToViewport(data), x = _this$framedGraphToVi.x, y = _this$framedGraphToVi.y;
          var size = this.scaleSize(data.size);
          if (!data.forceLabel && size < this.settings.labelRenderedSizeThreshold) continue;
          if (x < -X_LABEL_MARGIN || x > this.width + X_LABEL_MARGIN || y < -Y_LABEL_MARGIN || y > this.height + Y_LABEL_MARGIN) continue;
          this.displayedNodeLabels.add(node);
          var defaultDrawNodeLabel = this.settings.defaultDrawNodeLabel;
          var nodeProgram = this.nodePrograms[data.type];
          var drawLabel = (nodeProgram === null || nodeProgram === void 0 ? void 0 : nodeProgram.drawLabel) || defaultDrawNodeLabel;
          drawLabel(context, _objectSpread2(_objectSpread2({
            key: node
          }, data), {}, {
            size,
            x,
            y
          }), this.settings);
        }
        return this;
      }
      /**
       * Method used to render edge labels, based on which node labels were
       * rendered.
       *
       * @return {Sigma}
       */
    }, {
      key: "renderEdgeLabels",
      value: function renderEdgeLabels() {
        if (!this.settings.renderEdgeLabels) return this;
        var context = this.canvasContexts.edgeLabels;
        context.clearRect(0, 0, this.width, this.height);
        var edgeLabelsToDisplay = edgeLabelsToDisplayFromNodes({
          graph: this.graph,
          hoveredNode: this.hoveredNode,
          displayedNodeLabels: this.displayedNodeLabels,
          highlightedNodes: this.highlightedNodes
        });
        extend(edgeLabelsToDisplay, this.edgesWithForcedLabels);
        var displayedLabels = /* @__PURE__ */ new Set();
        for (var i = 0, l = edgeLabelsToDisplay.length; i < l; i++) {
          var edge = edgeLabelsToDisplay[i], extremities = this.graph.extremities(edge), sourceData = this.nodeDataCache[extremities[0]], targetData = this.nodeDataCache[extremities[1]], edgeData = this.edgeDataCache[edge];
          if (displayedLabels.has(edge)) continue;
          if (edgeData.hidden || sourceData.hidden || targetData.hidden) {
            continue;
          }
          var defaultDrawEdgeLabel = this.settings.defaultDrawEdgeLabel;
          var edgeProgram = this.edgePrograms[edgeData.type];
          var drawLabel = (edgeProgram === null || edgeProgram === void 0 ? void 0 : edgeProgram.drawLabel) || defaultDrawEdgeLabel;
          drawLabel(context, _objectSpread2(_objectSpread2({
            key: edge
          }, edgeData), {}, {
            size: this.scaleSize(edgeData.size)
          }), _objectSpread2(_objectSpread2(_objectSpread2({
            key: extremities[0]
          }, sourceData), this.framedGraphToViewport(sourceData)), {}, {
            size: this.scaleSize(sourceData.size)
          }), _objectSpread2(_objectSpread2(_objectSpread2({
            key: extremities[1]
          }, targetData), this.framedGraphToViewport(targetData)), {}, {
            size: this.scaleSize(targetData.size)
          }), this.settings);
          displayedLabels.add(edge);
        }
        this.displayedEdgeLabels = displayedLabels;
        return this;
      }
      /**
       * Method used to render the highlighted nodes.
       *
       * @return {Sigma}
       */
    }, {
      key: "renderHighlightedNodes",
      value: function renderHighlightedNodes() {
        var _this7 = this;
        var context = this.canvasContexts.hovers;
        context.clearRect(0, 0, this.width, this.height);
        var render = function render2(node) {
          var data = _this7.nodeDataCache[node];
          var _this7$framedGraphToV = _this7.framedGraphToViewport(data), x = _this7$framedGraphToV.x, y = _this7$framedGraphToV.y;
          var size = _this7.scaleSize(data.size);
          var defaultDrawNodeHover = _this7.settings.defaultDrawNodeHover;
          var nodeProgram = _this7.nodePrograms[data.type];
          var drawHover = (nodeProgram === null || nodeProgram === void 0 ? void 0 : nodeProgram.drawHover) || defaultDrawNodeHover;
          drawHover(context, _objectSpread2(_objectSpread2({
            key: node
          }, data), {}, {
            size,
            x,
            y
          }), _this7.settings);
        };
        var nodesToRender = [];
        if (this.hoveredNode && !this.nodeDataCache[this.hoveredNode].hidden) {
          nodesToRender.push(this.hoveredNode);
        }
        this.highlightedNodes.forEach(function(node) {
          if (node !== _this7.hoveredNode) nodesToRender.push(node);
        });
        nodesToRender.forEach(function(node) {
          return render(node);
        });
        var nodesPerPrograms = {};
        nodesToRender.forEach(function(node) {
          var type2 = _this7.nodeDataCache[node].type;
          nodesPerPrograms[type2] = (nodesPerPrograms[type2] || 0) + 1;
        });
        for (var type in this.nodeHoverPrograms) {
          this.nodeHoverPrograms[type].reallocate(nodesPerPrograms[type] || 0);
          nodesPerPrograms[type] = 0;
        }
        nodesToRender.forEach(function(node) {
          var data = _this7.nodeDataCache[node];
          _this7.nodeHoverPrograms[data.type].process(0, nodesPerPrograms[data.type]++, data);
        });
        this.webGLContexts.hoverNodes.clear(this.webGLContexts.hoverNodes.COLOR_BUFFER_BIT);
        var renderParams = this.getRenderParams();
        for (var _type6 in this.nodeHoverPrograms) {
          var program = this.nodeHoverPrograms[_type6];
          program.render(renderParams);
        }
      }
      /**
       * Method used to schedule a hover render.
       *
       */
    }, {
      key: "scheduleHighlightedNodesRender",
      value: function scheduleHighlightedNodesRender() {
        var _this8 = this;
        if (this.renderHighlightedNodesFrame || this.renderFrame) return;
        this.renderHighlightedNodesFrame = requestAnimationFrame(function() {
          _this8.renderHighlightedNodesFrame = null;
          _this8.renderHighlightedNodes();
          _this8.renderEdgeLabels();
        });
      }
      /**
       * Method used to render.
       *
       * @return {Sigma}
       */
    }, {
      key: "render",
      value: function render() {
        var _this9 = this;
        this.emit("beforeRender");
        var exitRender = function exitRender2() {
          _this9.emit("afterRender");
          return _this9;
        };
        if (this.renderFrame) {
          cancelAnimationFrame(this.renderFrame);
          this.renderFrame = null;
        }
        this.resize();
        if (this.needToProcess) this.process();
        this.needToProcess = false;
        this.clear();
        this.pickingLayers.forEach(function(layer) {
          return _this9.resetWebGLTexture(layer);
        });
        if (!this.graph.order) return exitRender();
        var mouseCaptor = this.mouseCaptor;
        var moving = this.camera.isAnimated() || mouseCaptor.isMoving || mouseCaptor.draggedEvents || mouseCaptor.currentWheelDirection;
        var cameraState = this.camera.getState();
        var viewportDimensions = this.getDimensions();
        var graphDimensions = this.getGraphDimensions();
        var padding = this.getStagePadding();
        this.matrix = matrixFromCamera(cameraState, viewportDimensions, graphDimensions, padding);
        this.invMatrix = matrixFromCamera(cameraState, viewportDimensions, graphDimensions, padding, true);
        this.correctionRatio = getMatrixImpact(this.matrix, cameraState, viewportDimensions);
        this.graphToViewportRatio = this.getGraphToViewportRatio();
        var params = this.getRenderParams();
        for (var type in this.nodePrograms) {
          var program = this.nodePrograms[type];
          program.render(params);
        }
        if (!this.settings.hideEdgesOnMove || !moving) {
          for (var _type7 in this.edgePrograms) {
            var _program2 = this.edgePrograms[_type7];
            _program2.render(params);
          }
        }
        if (this.settings.hideLabelsOnMove && moving) return exitRender();
        this.renderLabels();
        this.renderEdgeLabels();
        this.renderHighlightedNodes();
        return exitRender();
      }
      /**
       * Add a node in the internal data structures.
       * @private
       * @param key The node's graphology ID
       */
    }, {
      key: "addNode",
      value: function addNode(key) {
        var attr = Object.assign({}, this.graph.getNodeAttributes(key));
        if (this.settings.nodeReducer) attr = this.settings.nodeReducer(key, attr);
        var data = applyNodeDefaults(this.settings, key, attr);
        this.nodeDataCache[key] = data;
        this.nodesWithForcedLabels["delete"](key);
        if (data.forceLabel && !data.hidden) this.nodesWithForcedLabels.add(key);
        this.highlightedNodes["delete"](key);
        if (data.highlighted && !data.hidden) this.highlightedNodes.add(key);
        if (this.settings.zIndex) {
          if (data.zIndex < this.nodeZExtent[0]) this.nodeZExtent[0] = data.zIndex;
          if (data.zIndex > this.nodeZExtent[1]) this.nodeZExtent[1] = data.zIndex;
        }
      }
      /**
       * Update a node the internal data structures.
       * @private
       * @param key The node's graphology ID
       */
    }, {
      key: "updateNode",
      value: function updateNode(key) {
        this.addNode(key);
        var data = this.nodeDataCache[key];
        this.normalizationFunction.applyTo(data);
      }
      /**
       * Remove a node from the internal data structures.
       * @private
       * @param key The node's graphology ID
       */
    }, {
      key: "removeNode",
      value: function removeNode(key) {
        delete this.nodeDataCache[key];
        delete this.nodeProgramIndex[key];
        this.highlightedNodes["delete"](key);
        if (this.hoveredNode === key) this.hoveredNode = null;
        this.nodesWithForcedLabels["delete"](key);
      }
      /**
       * Add an edge into the internal data structures.
       * @private
       * @param key The edge's graphology ID
       */
    }, {
      key: "addEdge",
      value: function addEdge(key) {
        var attr = Object.assign({}, this.graph.getEdgeAttributes(key));
        if (this.settings.edgeReducer) attr = this.settings.edgeReducer(key, attr);
        var data = applyEdgeDefaults(this.settings, key, attr);
        this.edgeDataCache[key] = data;
        this.edgesWithForcedLabels["delete"](key);
        if (data.forceLabel && !data.hidden) this.edgesWithForcedLabels.add(key);
        if (this.settings.zIndex) {
          if (data.zIndex < this.edgeZExtent[0]) this.edgeZExtent[0] = data.zIndex;
          if (data.zIndex > this.edgeZExtent[1]) this.edgeZExtent[1] = data.zIndex;
        }
      }
      /**
       * Update an edge in the internal data structures.
       * @private
       * @param key The edge's graphology ID
       */
    }, {
      key: "updateEdge",
      value: function updateEdge(key) {
        this.addEdge(key);
      }
      /**
       * Remove an edge from the internal data structures.
       * @private
       * @param key The edge's graphology ID
       */
    }, {
      key: "removeEdge",
      value: function removeEdge(key) {
        delete this.edgeDataCache[key];
        delete this.edgeProgramIndex[key];
        if (this.hoveredEdge === key) this.hoveredEdge = null;
        this.edgesWithForcedLabels["delete"](key);
      }
      /**
       * Clear all indices related to nodes.
       * @private
       */
    }, {
      key: "clearNodeIndices",
      value: function clearNodeIndices() {
        this.labelGrid = new LabelGrid();
        this.nodeExtent = {
          x: [0, 1],
          y: [0, 1]
        };
        this.nodeDataCache = {};
        this.edgeProgramIndex = {};
        this.nodesWithForcedLabels = /* @__PURE__ */ new Set();
        this.nodeZExtent = [Infinity, -Infinity];
        this.highlightedNodes = /* @__PURE__ */ new Set();
      }
      /**
       * Clear all indices related to edges.
       * @private
       */
    }, {
      key: "clearEdgeIndices",
      value: function clearEdgeIndices() {
        this.edgeDataCache = {};
        this.edgeProgramIndex = {};
        this.edgesWithForcedLabels = /* @__PURE__ */ new Set();
        this.edgeZExtent = [Infinity, -Infinity];
      }
      /**
       * Clear all indices.
       * @private
       */
    }, {
      key: "clearIndices",
      value: function clearIndices() {
        this.clearEdgeIndices();
        this.clearNodeIndices();
      }
      /**
       * Clear all graph state related to nodes.
       * @private
       */
    }, {
      key: "clearNodeState",
      value: function clearNodeState() {
        this.displayedNodeLabels = /* @__PURE__ */ new Set();
        this.highlightedNodes = /* @__PURE__ */ new Set();
        this.hoveredNode = null;
      }
      /**
       * Clear all graph state related to edges.
       * @private
       */
    }, {
      key: "clearEdgeState",
      value: function clearEdgeState() {
        this.displayedEdgeLabels = /* @__PURE__ */ new Set();
        this.highlightedNodes = /* @__PURE__ */ new Set();
        this.hoveredEdge = null;
      }
      /**
       * Clear all graph state.
       * @private
       */
    }, {
      key: "clearState",
      value: function clearState() {
        this.clearEdgeState();
        this.clearNodeState();
      }
      /**
       * Add the node data to its program.
       * @private
       * @param node The node's graphology ID
       * @param fingerprint A fingerprint used to identity the node with picking
       * @param position The index where to place the node in the program
       */
    }, {
      key: "addNodeToProgram",
      value: function addNodeToProgram(node, fingerprint, position) {
        var data = this.nodeDataCache[node];
        var nodeProgram = this.nodePrograms[data.type];
        if (!nodeProgram) throw new Error('Sigma: could not find a suitable program for node type "'.concat(data.type, '"!'));
        nodeProgram.process(fingerprint, position, data);
        this.nodeProgramIndex[node] = position;
      }
      /**
       * Add the edge data to its program.
       * @private
       * @param edge The edge's graphology ID
       * @param fingerprint A fingerprint used to identity the edge with picking
       * @param position The index where to place the edge in the program
       */
    }, {
      key: "addEdgeToProgram",
      value: function addEdgeToProgram(edge, fingerprint, position) {
        var data = this.edgeDataCache[edge];
        var edgeProgram = this.edgePrograms[data.type];
        if (!edgeProgram) throw new Error('Sigma: could not find a suitable program for edge type "'.concat(data.type, '"!'));
        var extremities = this.graph.extremities(edge), sourceData = this.nodeDataCache[extremities[0]], targetData = this.nodeDataCache[extremities[1]];
        edgeProgram.process(fingerprint, position, sourceData, targetData, data);
        this.edgeProgramIndex[edge] = position;
      }
      /**---------------------------------------------------------------------------
       * Public API.
       **---------------------------------------------------------------------------
       */
      /**
       * Function used to get the render params.
       *
       * @return {RenderParams}
       */
    }, {
      key: "getRenderParams",
      value: function getRenderParams() {
        return {
          matrix: this.matrix,
          invMatrix: this.invMatrix,
          width: this.width,
          height: this.height,
          pixelRatio: this.pixelRatio,
          zoomRatio: this.camera.ratio,
          cameraAngle: this.camera.angle,
          sizeRatio: 1 / this.scaleSize(),
          correctionRatio: this.correctionRatio,
          downSizingRatio: this.pickingDownSizingRatio,
          minEdgeThickness: this.settings.minEdgeThickness,
          antiAliasingFeather: this.settings.antiAliasingFeather
        };
      }
      /**
       * Function used to retrieve the actual stage padding value.
       *
       * @return {number}
       */
    }, {
      key: "getStagePadding",
      value: function getStagePadding() {
        var _this$settings = this.settings, stagePadding = _this$settings.stagePadding, autoRescale = _this$settings.autoRescale;
        return autoRescale ? stagePadding || 0 : 0;
      }
      /**
       * Function used to create a layer element.
       *
       * @param {string} id - Context's id.
       * @param {string} tag - The HTML tag to use.
       * @param options
       * @return {Sigma}
       */
    }, {
      key: "createLayer",
      value: function createLayer(id, tag) {
        var options = arguments.length > 2 && arguments[2] !== void 0 ? arguments[2] : {};
        if (this.elements[id]) throw new Error('Sigma: a layer named "'.concat(id, '" already exists'));
        var element = createElement(tag, {
          position: "absolute"
        }, {
          "class": "sigma-".concat(id)
        });
        if (options.style) Object.assign(element.style, options.style);
        this.elements[id] = element;
        if ("beforeLayer" in options && options.beforeLayer) {
          this.elements[options.beforeLayer].before(element);
        } else if ("afterLayer" in options && options.afterLayer) {
          this.elements[options.afterLayer].after(element);
        } else {
          this.container.appendChild(element);
        }
        return element;
      }
      /**
       * Function used to create a canvas element.
       *
       * @param {string} id - Context's id.
       * @param options
       * @return {Sigma}
       */
    }, {
      key: "createCanvas",
      value: function createCanvas(id) {
        var options = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : {};
        return this.createLayer(id, "canvas", options);
      }
      /**
       * Function used to create a canvas context and add the relevant DOM elements.
       *
       * @param  {string} id - Context's id.
       * @param  options
       * @return {Sigma}
       */
    }, {
      key: "createCanvasContext",
      value: function createCanvasContext(id) {
        var options = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : {};
        var canvas = this.createCanvas(id, options);
        var contextOptions = {
          preserveDrawingBuffer: false,
          antialias: false
        };
        this.canvasContexts[id] = canvas.getContext("2d", contextOptions);
        return this;
      }
      /**
       * Function used to create a WebGL context and add the relevant DOM
       * elements.
       *
       * @param  {string}  id      - Context's id.
       * @param  {object?} options - #getContext params to override (optional)
       * @return {WebGLRenderingContext}
       */
    }, {
      key: "createWebGLContext",
      value: function createWebGLContext(id) {
        var options = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : {};
        var canvas = (options === null || options === void 0 ? void 0 : options.canvas) || this.createCanvas(id, options);
        if (options.hidden) canvas.remove();
        var contextOptions = _objectSpread2({
          preserveDrawingBuffer: false,
          antialias: false
        }, options);
        var context;
        context = canvas.getContext("webgl2", contextOptions);
        if (!context) context = canvas.getContext("webgl", contextOptions);
        if (!context) context = canvas.getContext("experimental-webgl", contextOptions);
        var gl = context;
        this.webGLContexts[id] = gl;
        gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        if (options.picking) {
          this.pickingLayers.add(id);
          var newFrameBuffer = gl.createFramebuffer();
          if (!newFrameBuffer) throw new Error("Sigma: cannot create a new frame buffer for layer ".concat(id));
          this.frameBuffers[id] = newFrameBuffer;
        }
        return gl;
      }
      /**
       * Function used to properly kill a layer.
       *
       * @param  {string} id - Layer id.
       * @return {Sigma}
       */
    }, {
      key: "killLayer",
      value: function killLayer(id) {
        var element = this.elements[id];
        if (!element) throw new Error("Sigma: cannot kill layer ".concat(id, ", which does not exist"));
        if (this.webGLContexts[id]) {
          var _gl$getExtension;
          var gl = this.webGLContexts[id];
          (_gl$getExtension = gl.getExtension("WEBGL_lose_context")) === null || _gl$getExtension === void 0 || _gl$getExtension.loseContext();
          delete this.webGLContexts[id];
        } else if (this.canvasContexts[id]) {
          delete this.canvasContexts[id];
        }
        element.remove();
        delete this.elements[id];
        return this;
      }
      /**
       * Method returning the renderer's camera.
       *
       * @return {Camera}
       */
    }, {
      key: "getCamera",
      value: function getCamera() {
        return this.camera;
      }
      /**
       * Method setting the renderer's camera.
       *
       * @param  {Camera} camera - New camera.
       * @return {Sigma}
       */
    }, {
      key: "setCamera",
      value: function setCamera(camera) {
        this.unbindCameraHandlers();
        this.camera = camera;
        this.bindCameraHandlers();
      }
      /**
       * Method returning the container DOM element.
       *
       * @return {HTMLElement}
       */
    }, {
      key: "getContainer",
      value: function getContainer() {
        return this.container;
      }
      /**
       * Method returning the renderer's graph.
       *
       * @return {Graph}
       */
    }, {
      key: "getGraph",
      value: function getGraph() {
        return this.graph;
      }
      /**
       * Method used to set the renderer's graph.
       *
       * @return {Graph}
       */
    }, {
      key: "setGraph",
      value: function setGraph(graph) {
        if (graph === this.graph) return;
        if (this.hoveredNode && !graph.hasNode(this.hoveredNode)) this.hoveredNode = null;
        if (this.hoveredEdge && !graph.hasEdge(this.hoveredEdge)) this.hoveredEdge = null;
        this.unbindGraphHandlers();
        if (this.checkEdgesEventsFrame !== null) {
          cancelAnimationFrame(this.checkEdgesEventsFrame);
          this.checkEdgesEventsFrame = null;
        }
        this.graph = graph;
        this.bindGraphHandlers();
        this.refresh();
      }
      /**
       * Method returning the mouse captor.
       *
       * @return {MouseCaptor}
       */
    }, {
      key: "getMouseCaptor",
      value: function getMouseCaptor() {
        return this.mouseCaptor;
      }
      /**
       * Method returning the touch captor.
       *
       * @return {TouchCaptor}
       */
    }, {
      key: "getTouchCaptor",
      value: function getTouchCaptor() {
        return this.touchCaptor;
      }
      /**
       * Method returning the current renderer's dimensions.
       *
       * @return {Dimensions}
       */
    }, {
      key: "getDimensions",
      value: function getDimensions() {
        return {
          width: this.width,
          height: this.height
        };
      }
      /**
       * Method returning the current graph's dimensions.
       *
       * @return {Dimensions}
       */
    }, {
      key: "getGraphDimensions",
      value: function getGraphDimensions() {
        var extent = this.customBBox || this.nodeExtent;
        return {
          width: extent.x[1] - extent.x[0] || 1,
          height: extent.y[1] - extent.y[0] || 1
        };
      }
      /**
       * Method used to get all the sigma node attributes.
       * It's useful for example to get the position of a node
       * and to get values that are set by the nodeReducer
       *
       * @param  {string} key - The node's key.
       * @return {NodeDisplayData | undefined} A copy of the desired node's attribute or undefined if not found
       */
    }, {
      key: "getNodeDisplayData",
      value: function getNodeDisplayData(key) {
        var node = this.nodeDataCache[key];
        return node ? Object.assign({}, node) : void 0;
      }
      /**
       * Method used to get all the sigma edge attributes.
       * It's useful for example to get values that are set by the edgeReducer.
       *
       * @param  {string} key - The edge's key.
       * @return {EdgeDisplayData | undefined} A copy of the desired edge's attribute or undefined if not found
       */
    }, {
      key: "getEdgeDisplayData",
      value: function getEdgeDisplayData(key) {
        var edge = this.edgeDataCache[key];
        return edge ? Object.assign({}, edge) : void 0;
      }
      /**
       * Method used to get the set of currently displayed node labels.
       *
       * @return {Set<string>} A set of node keys whose label is displayed.
       */
    }, {
      key: "getNodeDisplayedLabels",
      value: function getNodeDisplayedLabels() {
        return new Set(this.displayedNodeLabels);
      }
      /**
       * Method used to get the set of currently displayed edge labels.
       *
       * @return {Set<string>} A set of edge keys whose label is displayed.
       */
    }, {
      key: "getEdgeDisplayedLabels",
      value: function getEdgeDisplayedLabels() {
        return new Set(this.displayedEdgeLabels);
      }
      /**
       * Method returning a copy of the settings collection.
       *
       * @return {Settings} A copy of the settings collection.
       */
    }, {
      key: "getSettings",
      value: function getSettings() {
        return _objectSpread2({}, this.settings);
      }
      /**
       * Method returning the current value for a given setting key.
       *
       * @param  {string} key - The setting key to get.
       * @return {any} The value attached to this setting key or undefined if not found
       */
    }, {
      key: "getSetting",
      value: function getSetting(key) {
        return this.settings[key];
      }
      /**
       * Method setting the value of a given setting key. Note that this will schedule
       * a new render next frame.
       *
       * @param  {string} key - The setting key to set.
       * @param  {any}    value - The value to set.
       * @return {Sigma}
       */
    }, {
      key: "setSetting",
      value: function setSetting(key, value) {
        var oldValues = _objectSpread2({}, this.settings);
        this.settings[key] = value;
        validateSettings(this.settings);
        this.handleSettingsUpdate(oldValues);
        this.scheduleRefresh();
        return this;
      }
      /**
       * Method updating the value of a given setting key using the provided function.
       * Note that this will schedule a new render next frame.
       *
       * @param  {string}   key     - The setting key to set.
       * @param  {function} updater - The update function.
       * @return {Sigma}
       */
    }, {
      key: "updateSetting",
      value: function updateSetting(key, updater) {
        this.setSetting(key, updater(this.settings[key]));
        return this;
      }
      /**
       * Method setting multiple settings at once.
       *
       * @param  {Partial<Settings>} settings - The settings to set.
       * @return {Sigma}
       */
    }, {
      key: "setSettings",
      value: function setSettings(settings) {
        var oldValues = _objectSpread2({}, this.settings);
        this.settings = _objectSpread2(_objectSpread2({}, this.settings), settings);
        validateSettings(this.settings);
        this.handleSettingsUpdate(oldValues);
        this.scheduleRefresh();
        return this;
      }
      /**
       * Method used to resize the renderer.
       *
       * @param  {boolean} force - If true, then resize is processed even if size is unchanged (optional).
       * @return {Sigma}
       */
    }, {
      key: "resize",
      value: function resize(force) {
        var previousWidth = this.width, previousHeight = this.height;
        this.width = this.container.offsetWidth;
        this.height = this.container.offsetHeight;
        this.pixelRatio = getPixelRatio();
        if (this.width === 0) {
          if (this.settings.allowInvalidContainer) this.width = 1;
          else throw new Error("Sigma: Container has no width. You can set the allowInvalidContainer setting to true to stop seeing this error.");
        }
        if (this.height === 0) {
          if (this.settings.allowInvalidContainer) this.height = 1;
          else throw new Error("Sigma: Container has no height. You can set the allowInvalidContainer setting to true to stop seeing this error.");
        }
        if (!force && previousWidth === this.width && previousHeight === this.height) return this;
        for (var id in this.elements) {
          var element = this.elements[id];
          element.style.width = this.width + "px";
          element.style.height = this.height + "px";
        }
        for (var _id in this.canvasContexts) {
          this.elements[_id].setAttribute("width", this.width * this.pixelRatio + "px");
          this.elements[_id].setAttribute("height", this.height * this.pixelRatio + "px");
          if (this.pixelRatio !== 1) this.canvasContexts[_id].scale(this.pixelRatio, this.pixelRatio);
        }
        for (var _id2 in this.webGLContexts) {
          this.elements[_id2].setAttribute("width", this.width * this.pixelRatio + "px");
          this.elements[_id2].setAttribute("height", this.height * this.pixelRatio + "px");
          var gl = this.webGLContexts[_id2];
          gl.viewport(0, 0, this.width * this.pixelRatio, this.height * this.pixelRatio);
          if (this.pickingLayers.has(_id2)) {
            var currentTexture = this.textures[_id2];
            if (currentTexture) gl.deleteTexture(currentTexture);
          }
        }
        this.emit("resize");
        return this;
      }
      /**
       * Method used to clear all the canvases.
       *
       * @return {Sigma}
       */
    }, {
      key: "clear",
      value: function clear() {
        this.emit("beforeClear");
        this.webGLContexts.nodes.bindFramebuffer(WebGLRenderingContext.FRAMEBUFFER, null);
        this.webGLContexts.nodes.clear(WebGLRenderingContext.COLOR_BUFFER_BIT);
        this.webGLContexts.edges.bindFramebuffer(WebGLRenderingContext.FRAMEBUFFER, null);
        this.webGLContexts.edges.clear(WebGLRenderingContext.COLOR_BUFFER_BIT);
        this.webGLContexts.hoverNodes.clear(WebGLRenderingContext.COLOR_BUFFER_BIT);
        this.canvasContexts.labels.clearRect(0, 0, this.width, this.height);
        this.canvasContexts.hovers.clearRect(0, 0, this.width, this.height);
        this.canvasContexts.edgeLabels.clearRect(0, 0, this.width, this.height);
        this.emit("afterClear");
        return this;
      }
      /**
       * Method used to refresh, i.e. force the renderer to reprocess graph
       * data and render, but keep the state.
       * - if a partialGraph is provided, we only reprocess those nodes & edges.
       * - if schedule is TRUE, we schedule a render instead of sync render
       * - if skipIndexation is TRUE, then labelGrid & program indexation are skipped (can be used if you haven't modify x, y, zIndex & size)
       *
       * @return {Sigma}
       */
    }, {
      key: "refresh",
      value: function refresh(opts) {
        var _this10 = this;
        var skipIndexation = (opts === null || opts === void 0 ? void 0 : opts.skipIndexation) !== void 0 ? opts === null || opts === void 0 ? void 0 : opts.skipIndexation : false;
        var schedule = (opts === null || opts === void 0 ? void 0 : opts.schedule) !== void 0 ? opts.schedule : false;
        var fullRefresh = !opts || !opts.partialGraph;
        if (fullRefresh) {
          this.clearEdgeIndices();
          this.clearNodeIndices();
          this.graph.forEachNode(function(node2) {
            return _this10.addNode(node2);
          });
          this.graph.forEachEdge(function(edge2) {
            return _this10.addEdge(edge2);
          });
        } else {
          var _opts$partialGraph, _opts$partialGraph2;
          var nodes = ((_opts$partialGraph = opts.partialGraph) === null || _opts$partialGraph === void 0 ? void 0 : _opts$partialGraph.nodes) || [];
          for (var i = 0, l = (nodes === null || nodes === void 0 ? void 0 : nodes.length) || 0; i < l; i++) {
            var node = nodes[i];
            this.updateNode(node);
            if (skipIndexation) {
              var programIndex = this.nodeProgramIndex[node];
              if (programIndex === void 0) throw new Error('Sigma: node "'.concat(node, `" can't be repaint`));
              this.addNodeToProgram(node, this.nodeIndices[node], programIndex);
            }
          }
          var edges = (opts === null || opts === void 0 || (_opts$partialGraph2 = opts.partialGraph) === null || _opts$partialGraph2 === void 0 ? void 0 : _opts$partialGraph2.edges) || [];
          for (var _i4 = 0, _l4 = edges.length; _i4 < _l4; _i4++) {
            var edge = edges[_i4];
            this.updateEdge(edge);
            if (skipIndexation) {
              var _programIndex = this.edgeProgramIndex[edge];
              if (_programIndex === void 0) throw new Error('Sigma: edge "'.concat(edge, `" can't be repaint`));
              this.addEdgeToProgram(edge, this.edgeIndices[edge], _programIndex);
            }
          }
        }
        if (fullRefresh || !skipIndexation) this.needToProcess = true;
        if (schedule) this.scheduleRender();
        else this.render();
        return this;
      }
      /**
       * Method used to schedule a render at the next available frame.
       * This method can be safely called on a same frame because it basically
       * debounces refresh to the next frame.
       *
       * @return {Sigma}
       */
    }, {
      key: "scheduleRender",
      value: function scheduleRender() {
        var _this11 = this;
        if (!this.renderFrame) {
          this.renderFrame = requestAnimationFrame(function() {
            _this11.render();
          });
        }
        return this;
      }
      /**
       * Method used to schedule a refresh (i.e. fully reprocess graph data and render)
       * at the next available frame.
       * This method can be safely called on a same frame because it basically
       * debounces refresh to the next frame.
       *
       * @return {Sigma}
       */
    }, {
      key: "scheduleRefresh",
      value: function scheduleRefresh(opts) {
        return this.refresh(_objectSpread2(_objectSpread2({}, opts), {}, {
          schedule: true
        }));
      }
      /**
       * Method used to (un)zoom, while preserving the position of a viewport point.
       * Used for instance to zoom "on the mouse cursor".
       *
       * @param viewportTarget
       * @param newRatio
       * @return {CameraState}
       */
    }, {
      key: "getViewportZoomedState",
      value: function getViewportZoomedState(viewportTarget, newRatio) {
        var _this$camera$getState = this.camera.getState(), ratio = _this$camera$getState.ratio, angle = _this$camera$getState.angle, x = _this$camera$getState.x, y = _this$camera$getState.y;
        var _this$settings2 = this.settings, minCameraRatio = _this$settings2.minCameraRatio, maxCameraRatio = _this$settings2.maxCameraRatio;
        if (typeof maxCameraRatio === "number") newRatio = Math.min(newRatio, maxCameraRatio);
        if (typeof minCameraRatio === "number") newRatio = Math.max(newRatio, minCameraRatio);
        var ratioDiff = newRatio / ratio;
        var center = {
          x: this.width / 2,
          y: this.height / 2
        };
        var graphMousePosition = this.viewportToFramedGraph(viewportTarget);
        var graphCenterPosition = this.viewportToFramedGraph(center);
        return {
          angle,
          x: (graphMousePosition.x - graphCenterPosition.x) * (1 - ratioDiff) + x,
          y: (graphMousePosition.y - graphCenterPosition.y) * (1 - ratioDiff) + y,
          ratio: newRatio
        };
      }
      /**
       * Method returning the abstract rectangle containing the graph according
       * to the camera's state.
       *
       * @return {object} - The view's rectangle.
       */
    }, {
      key: "viewRectangle",
      value: function viewRectangle() {
        var p1 = this.viewportToFramedGraph({
          x: 0,
          y: 0
        }), p2 = this.viewportToFramedGraph({
          x: this.width,
          y: 0
        }), h = this.viewportToFramedGraph({
          x: 0,
          y: this.height
        });
        return {
          x1: p1.x,
          y1: p1.y,
          x2: p2.x,
          y2: p2.y,
          height: p2.y - h.y
        };
      }
      /**
       * Method returning the coordinates of a point from the framed graph system to the viewport system. It allows
       * overriding anything that is used to get the translation matrix, or even the matrix itself.
       *
       * Be careful if overriding dimensions, padding or cameraState, as the computation of the matrix is not the lightest
       * of computations.
       */
    }, {
      key: "framedGraphToViewport",
      value: function framedGraphToViewport(coordinates) {
        var override = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : {};
        var recomputeMatrix = !!override.cameraState || !!override.viewportDimensions || !!override.graphDimensions;
        var matrix = override.matrix ? override.matrix : recomputeMatrix ? matrixFromCamera(override.cameraState || this.camera.getState(), override.viewportDimensions || this.getDimensions(), override.graphDimensions || this.getGraphDimensions(), override.padding || this.getStagePadding()) : this.matrix;
        var viewportPos = multiplyVec2(matrix, coordinates);
        return {
          x: (1 + viewportPos.x) * this.width / 2,
          y: (1 - viewportPos.y) * this.height / 2
        };
      }
      /**
       * Method returning the coordinates of a point from the viewport system to the framed graph system. It allows
       * overriding anything that is used to get the translation matrix, or even the matrix itself.
       *
       * Be careful if overriding dimensions, padding or cameraState, as the computation of the matrix is not the lightest
       * of computations.
       */
    }, {
      key: "viewportToFramedGraph",
      value: function viewportToFramedGraph(coordinates) {
        var override = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : {};
        var recomputeMatrix = !!override.cameraState || !!override.viewportDimensions || !override.graphDimensions;
        var invMatrix = override.matrix ? override.matrix : recomputeMatrix ? matrixFromCamera(override.cameraState || this.camera.getState(), override.viewportDimensions || this.getDimensions(), override.graphDimensions || this.getGraphDimensions(), override.padding || this.getStagePadding(), true) : this.invMatrix;
        var res = multiplyVec2(invMatrix, {
          x: coordinates.x / this.width * 2 - 1,
          y: 1 - coordinates.y / this.height * 2
        });
        if (isNaN(res.x)) res.x = 0;
        if (isNaN(res.y)) res.y = 0;
        return res;
      }
      /**
       * Method used to translate a point's coordinates from the viewport system (pixel distance from the top-left of the
       * stage) to the graph system (the reference system of data as they are in the given graph instance).
       *
       * This method accepts an optional camera which can be useful if you need to translate coordinates
       * based on a different view than the one being currently being displayed on screen.
       *
       * @param {Coordinates}                  viewportPoint
       * @param {CoordinateConversionOverride} override
       */
    }, {
      key: "viewportToGraph",
      value: function viewportToGraph(viewportPoint) {
        var override = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : {};
        return this.normalizationFunction.inverse(this.viewportToFramedGraph(viewportPoint, override));
      }
      /**
       * Method used to translate a point's coordinates from the graph system (the reference system of data as they are in
       * the given graph instance) to the viewport system (pixel distance from the top-left of the stage).
       *
       * This method accepts an optional camera which can be useful if you need to translate coordinates
       * based on a different view than the one being currently being displayed on screen.
       *
       * @param {Coordinates}                  graphPoint
       * @param {CoordinateConversionOverride} override
       */
    }, {
      key: "graphToViewport",
      value: function graphToViewport(graphPoint) {
        var override = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : {};
        return this.framedGraphToViewport(this.normalizationFunction(graphPoint), override);
      }
      /**
       * Method returning the distance multiplier between the graph system and the
       * viewport system.
       */
    }, {
      key: "getGraphToViewportRatio",
      value: function getGraphToViewportRatio() {
        var graphP1 = {
          x: 0,
          y: 0
        };
        var graphP2 = {
          x: 1,
          y: 1
        };
        var graphD = Math.sqrt(Math.pow(graphP1.x - graphP2.x, 2) + Math.pow(graphP1.y - graphP2.y, 2));
        var viewportP1 = this.graphToViewport(graphP1);
        var viewportP2 = this.graphToViewport(graphP2);
        var viewportD = Math.sqrt(Math.pow(viewportP1.x - viewportP2.x, 2) + Math.pow(viewportP1.y - viewportP2.y, 2));
        return viewportD / graphD;
      }
      /**
       * Method returning the graph's bounding box.
       *
       * @return {{ x: Extent, y: Extent }}
       */
    }, {
      key: "getBBox",
      value: function getBBox() {
        return this.nodeExtent;
      }
      /**
       * Method returning the graph's custom bounding box, if any.
       *
       * @return {{ x: Extent, y: Extent } | null}
       */
    }, {
      key: "getCustomBBox",
      value: function getCustomBBox() {
        return this.customBBox;
      }
      /**
       * Method used to override the graph's bounding box with a custom one. Give `null` as the argument to stop overriding.
       *
       * @return {Sigma}
       */
    }, {
      key: "setCustomBBox",
      value: function setCustomBBox(customBBox) {
        this.customBBox = customBBox;
        this.scheduleRender();
        return this;
      }
      /**
       * Method used to shut the container & release event listeners.
       *
       * @return {undefined}
       */
    }, {
      key: "kill",
      value: function kill() {
        this.emit("kill");
        this.removeAllListeners();
        this.unbindCameraHandlers();
        window.removeEventListener("resize", this.activeListeners.handleResize);
        this.mouseCaptor.kill();
        this.touchCaptor.kill();
        this.unbindGraphHandlers();
        this.clearIndices();
        this.clearState();
        this.nodeDataCache = {};
        this.edgeDataCache = {};
        this.highlightedNodes.clear();
        if (this.renderFrame) {
          cancelAnimationFrame(this.renderFrame);
          this.renderFrame = null;
        }
        if (this.renderHighlightedNodesFrame) {
          cancelAnimationFrame(this.renderHighlightedNodesFrame);
          this.renderHighlightedNodesFrame = null;
        }
        var container = this.container;
        while (container.firstChild) container.removeChild(container.firstChild);
        for (var type in this.nodePrograms) {
          this.nodePrograms[type].kill();
        }
        for (var _type8 in this.nodeHoverPrograms) {
          this.nodeHoverPrograms[_type8].kill();
        }
        for (var _type9 in this.edgePrograms) {
          this.edgePrograms[_type9].kill();
        }
        this.nodePrograms = {};
        this.nodeHoverPrograms = {};
        this.edgePrograms = {};
        for (var id in this.elements) {
          this.killLayer(id);
        }
        this.canvasContexts = {};
        this.webGLContexts = {};
        this.elements = {};
      }
      /**
       * Method used to scale the given size according to the camera's ratio, i.e.
       * zooming state.
       *
       * @param  {number?} size -        The size to scale (node size, edge thickness etc.).
       * @param  {number?} cameraRatio - A camera ratio (defaults to the actual camera ratio).
       * @return {number}              - The scaled size.
       */
    }, {
      key: "scaleSize",
      value: function scaleSize() {
        var size = arguments.length > 0 && arguments[0] !== void 0 ? arguments[0] : 1;
        var cameraRatio = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : this.camera.ratio;
        return size / this.settings.zoomToSizeRatioFunction(cameraRatio) * (this.getSetting("itemSizesReference") === "positions" ? cameraRatio * this.graphToViewportRatio : 1);
      }
      /**
       * Method that returns the collection of all used canvases.
       * At the moment, the instantiated canvases are the following, and in the
       * following order in the DOM:
       * - `edges`
       * - `nodes`
       * - `edgeLabels`
       * - `labels`
       * - `hovers`
       * - `hoverNodes`
       * - `mouse`
       *
       * @return {PlainObject<HTMLCanvasElement>} - The collection of canvases.
       */
    }, {
      key: "getCanvases",
      value: function getCanvases() {
        var res = {};
        for (var layer in this.elements) if (this.elements[layer] instanceof HTMLCanvasElement) res[layer] = this.elements[layer];
        return res;
      }
    }]);
  })(TypedEventEmitter);
  var Sigma = Sigma$1;

  // node_modules/sigma/rendering/dist/sigma-rendering.esm.js
  var _WebGLRenderingContex$32 = WebGLRenderingContext;
  var UNSIGNED_BYTE$32 = _WebGLRenderingContex$32.UNSIGNED_BYTE;
  var FLOAT$32 = _WebGLRenderingContex$32.FLOAT;
  var SHADER_SOURCE$42 = (
    /*glsl*/
    "\nattribute vec4 a_id;\nattribute vec4 a_color;\nattribute vec2 a_normal;\nattribute float a_normalCoef;\nattribute vec2 a_positionStart;\nattribute vec2 a_positionEnd;\nattribute float a_positionCoef;\nattribute float a_sourceRadius;\nattribute float a_targetRadius;\nattribute float a_sourceRadiusCoef;\nattribute float a_targetRadiusCoef;\n\nuniform mat3 u_matrix;\nuniform float u_zoomRatio;\nuniform float u_sizeRatio;\nuniform float u_pixelRatio;\nuniform float u_correctionRatio;\nuniform float u_minEdgeThickness;\nuniform float u_lengthToThicknessRatio;\nuniform float u_feather;\n\nvarying vec4 v_color;\nvarying vec2 v_normal;\nvarying float v_thickness;\nvarying float v_feather;\n\nconst float bias = 255.0 / 254.0;\n\nvoid main() {\n  float minThickness = u_minEdgeThickness;\n\n  vec2 normal = a_normal * a_normalCoef;\n  vec2 position = a_positionStart * (1.0 - a_positionCoef) + a_positionEnd * a_positionCoef;\n\n  float normalLength = length(normal);\n  vec2 unitNormal = normal / normalLength;\n\n  // These first computations are taken from edge.vert.glsl. Please read it to\n  // get better comments on what's happening:\n  float pixelsThickness = max(normalLength, minThickness * u_sizeRatio);\n  float webGLThickness = pixelsThickness * u_correctionRatio / u_sizeRatio;\n\n  // Here, we move the point to leave space for the arrow heads:\n  // Source arrow head\n  float sourceRadius = a_sourceRadius * a_sourceRadiusCoef;\n  float sourceDirection = sign(sourceRadius);\n  float webGLSourceRadius = sourceDirection * sourceRadius * 2.0 * u_correctionRatio / u_sizeRatio;\n  float webGLSourceArrowHeadLength = webGLThickness * u_lengthToThicknessRatio * 2.0;\n  vec2 sourceCompensationVector =\n    vec2(-sourceDirection * unitNormal.y, sourceDirection * unitNormal.x)\n    * (webGLSourceRadius + webGLSourceArrowHeadLength);\n    \n  // Target arrow head\n  float targetRadius = a_targetRadius * a_targetRadiusCoef;\n  float targetDirection = sign(targetRadius);\n  float webGLTargetRadius = targetDirection * targetRadius * 2.0 * u_correctionRatio / u_sizeRatio;\n  float webGLTargetArrowHeadLength = webGLThickness * u_lengthToThicknessRatio * 2.0;\n  vec2 targetCompensationVector =\n  vec2(-targetDirection * unitNormal.y, targetDirection * unitNormal.x)\n    * (webGLTargetRadius + webGLTargetArrowHeadLength);\n\n  // Here is the proper position of the vertex\n  gl_Position = vec4((u_matrix * vec3(position + unitNormal * webGLThickness + sourceCompensationVector + targetCompensationVector, 1)).xy, 0, 1);\n\n  v_thickness = webGLThickness / u_zoomRatio;\n\n  v_normal = unitNormal;\n\n  v_feather = u_feather * u_correctionRatio / u_zoomRatio / u_pixelRatio * 2.0;\n\n  #ifdef PICKING_MODE\n  // For picking mode, we use the ID as the color:\n  v_color = a_id;\n  #else\n  // For normal mode, we use the color:\n  v_color = a_color;\n  #endif\n\n  v_color.a *= bias;\n}\n"
  );
  var VERTEX_SHADER_SOURCE$22 = SHADER_SOURCE$42;
  var _WebGLRenderingContex$22 = WebGLRenderingContext;
  var UNSIGNED_BYTE$22 = _WebGLRenderingContex$22.UNSIGNED_BYTE;
  var FLOAT$22 = _WebGLRenderingContex$22.FLOAT;
  var UNIFORMS$22 = ["u_matrix", "u_zoomRatio", "u_sizeRatio", "u_correctionRatio", "u_pixelRatio", "u_feather", "u_minEdgeThickness", "u_lengthToThicknessRatio"];
  var DEFAULT_EDGE_DOUBLE_CLAMPED_PROGRAM_OPTIONS = {
    lengthToThicknessRatio: DEFAULT_EDGE_ARROW_HEAD_PROGRAM_OPTIONS.lengthToThicknessRatio
  };
  function createEdgeDoubleClampedProgram(inputOptions) {
    var options = _objectSpread2(_objectSpread2({}, DEFAULT_EDGE_DOUBLE_CLAMPED_PROGRAM_OPTIONS), inputOptions || {});
    return /* @__PURE__ */ (function(_EdgeProgram) {
      function EdgeDoubleClampedProgram2() {
        _classCallCheck(this, EdgeDoubleClampedProgram2);
        return _callSuper(this, EdgeDoubleClampedProgram2, arguments);
      }
      _inherits(EdgeDoubleClampedProgram2, _EdgeProgram);
      return _createClass(EdgeDoubleClampedProgram2, [{
        key: "getDefinition",
        value: function getDefinition() {
          return {
            VERTICES: 6,
            VERTEX_SHADER_SOURCE: VERTEX_SHADER_SOURCE$22,
            FRAGMENT_SHADER_SOURCE,
            METHOD: WebGLRenderingContext.TRIANGLES,
            UNIFORMS: UNIFORMS$22,
            ATTRIBUTES: [{
              name: "a_positionStart",
              size: 2,
              type: FLOAT$22
            }, {
              name: "a_positionEnd",
              size: 2,
              type: FLOAT$22
            }, {
              name: "a_normal",
              size: 2,
              type: FLOAT$22
            }, {
              name: "a_color",
              size: 4,
              type: UNSIGNED_BYTE$22,
              normalized: true
            }, {
              name: "a_id",
              size: 4,
              type: UNSIGNED_BYTE$22,
              normalized: true
            }, {
              name: "a_sourceRadius",
              size: 1,
              type: FLOAT$22
            }, {
              name: "a_targetRadius",
              size: 1,
              type: FLOAT$22
            }],
            CONSTANT_ATTRIBUTES: [
              // If 0, then position will be a_positionStart
              // If 1, then position will be a_positionEnd
              {
                name: "a_positionCoef",
                size: 1,
                type: FLOAT$22
              },
              {
                name: "a_normalCoef",
                size: 1,
                type: FLOAT$22
              },
              {
                name: "a_sourceRadiusCoef",
                size: 1,
                type: FLOAT$22
              },
              {
                name: "a_targetRadiusCoef",
                size: 1,
                type: FLOAT$22
              }
            ],
            CONSTANT_DATA: [[0, 1, -1, 0], [0, -1, 1, 0], [1, 1, 0, 1], [1, 1, 0, 1], [0, -1, 1, 0], [1, -1, 0, -1]]
          };
        }
      }, {
        key: "processVisibleItem",
        value: function processVisibleItem(edgeIndex, startIndex, sourceData, targetData, data) {
          var thickness = data.size || 1;
          var x1 = sourceData.x;
          var y1 = sourceData.y;
          var x2 = targetData.x;
          var y2 = targetData.y;
          var color = floatColor(data.color);
          var dx = x2 - x1;
          var dy = y2 - y1;
          var sourceRadius = sourceData.size || 1;
          var targetRadius = targetData.size || 1;
          var len = dx * dx + dy * dy;
          var n1 = 0;
          var n2 = 0;
          if (len) {
            len = 1 / Math.sqrt(len);
            n1 = -dy * len * thickness;
            n2 = dx * len * thickness;
          }
          var array = this.array;
          array[startIndex++] = x1;
          array[startIndex++] = y1;
          array[startIndex++] = x2;
          array[startIndex++] = y2;
          array[startIndex++] = n1;
          array[startIndex++] = n2;
          array[startIndex++] = color;
          array[startIndex++] = edgeIndex;
          array[startIndex++] = sourceRadius;
          array[startIndex++] = targetRadius;
        }
      }, {
        key: "setUniforms",
        value: function setUniforms(params, _ref) {
          var gl = _ref.gl, uniformLocations = _ref.uniformLocations;
          var u_matrix = uniformLocations.u_matrix, u_zoomRatio = uniformLocations.u_zoomRatio, u_feather = uniformLocations.u_feather, u_pixelRatio = uniformLocations.u_pixelRatio, u_correctionRatio = uniformLocations.u_correctionRatio, u_sizeRatio = uniformLocations.u_sizeRatio, u_minEdgeThickness = uniformLocations.u_minEdgeThickness, u_lengthToThicknessRatio = uniformLocations.u_lengthToThicknessRatio;
          gl.uniformMatrix3fv(u_matrix, false, params.matrix);
          gl.uniform1f(u_zoomRatio, params.zoomRatio);
          gl.uniform1f(u_sizeRatio, params.sizeRatio);
          gl.uniform1f(u_correctionRatio, params.correctionRatio);
          gl.uniform1f(u_pixelRatio, params.pixelRatio);
          gl.uniform1f(u_feather, params.antiAliasingFeather);
          gl.uniform1f(u_minEdgeThickness, params.minEdgeThickness);
          gl.uniform1f(u_lengthToThicknessRatio, options.lengthToThicknessRatio);
        }
      }]);
    })(EdgeProgram);
  }
  var EdgeDoubleClampedProgram = createEdgeDoubleClampedProgram();
  function createEdgeDoubleArrowProgram(inputOptions) {
    return createEdgeCompoundProgram([createEdgeDoubleClampedProgram(inputOptions), createEdgeArrowHeadProgram(inputOptions), createEdgeArrowHeadProgram(_objectSpread2(_objectSpread2({}, inputOptions), {}, {
      extremity: "source"
    }))]);
  }
  var EdgeDoubleArrowProgram = createEdgeDoubleArrowProgram();
  var SHADER_SOURCE$32 = (
    /*glsl*/
    "\nprecision mediump float;\n\nvarying vec4 v_color;\n\nvoid main(void) {\n  gl_FragColor = v_color;\n}\n"
  );
  var FRAGMENT_SHADER_SOURCE$12 = SHADER_SOURCE$32;
  var SHADER_SOURCE$22 = (
    /*glsl*/
    "\nattribute vec4 a_id;\nattribute vec4 a_color;\nattribute vec2 a_position;\n\nuniform mat3 u_matrix;\n\nvarying vec4 v_color;\n\nconst float bias = 255.0 / 254.0;\n\nvoid main() {\n  // Scale from [[-1 1] [-1 1]] to the container:\n  gl_Position = vec4(\n    (u_matrix * vec3(a_position, 1)).xy,\n    0,\n    1\n  );\n\n  #ifdef PICKING_MODE\n  // For picking mode, we use the ID as the color:\n  v_color = a_id;\n  #else\n  // For normal mode, we use the color:\n  v_color = a_color;\n  #endif\n\n  v_color.a *= bias;\n}\n"
  );
  var VERTEX_SHADER_SOURCE$12 = SHADER_SOURCE$22;
  var _WebGLRenderingContex$12 = WebGLRenderingContext;
  var UNSIGNED_BYTE$12 = _WebGLRenderingContex$12.UNSIGNED_BYTE;
  var FLOAT$12 = _WebGLRenderingContex$12.FLOAT;
  var UNIFORMS$12 = ["u_matrix"];
  var EdgeLineProgram = /* @__PURE__ */ (function(_EdgeProgram) {
    function EdgeLineProgram2() {
      _classCallCheck(this, EdgeLineProgram2);
      return _callSuper(this, EdgeLineProgram2, arguments);
    }
    _inherits(EdgeLineProgram2, _EdgeProgram);
    return _createClass(EdgeLineProgram2, [{
      key: "getDefinition",
      value: function getDefinition() {
        return {
          VERTICES: 2,
          VERTEX_SHADER_SOURCE: VERTEX_SHADER_SOURCE$12,
          FRAGMENT_SHADER_SOURCE: FRAGMENT_SHADER_SOURCE$12,
          METHOD: WebGLRenderingContext.LINES,
          UNIFORMS: UNIFORMS$12,
          ATTRIBUTES: [{
            name: "a_position",
            size: 2,
            type: FLOAT$12
          }, {
            name: "a_color",
            size: 4,
            type: UNSIGNED_BYTE$12,
            normalized: true
          }, {
            name: "a_id",
            size: 4,
            type: UNSIGNED_BYTE$12,
            normalized: true
          }]
        };
      }
    }, {
      key: "processVisibleItem",
      value: function processVisibleItem(edgeIndex, startIndex, sourceData, targetData, data) {
        var array = this.array;
        var x1 = sourceData.x;
        var y1 = sourceData.y;
        var x2 = targetData.x;
        var y2 = targetData.y;
        var color = floatColor(data.color);
        array[startIndex++] = x1;
        array[startIndex++] = y1;
        array[startIndex++] = color;
        array[startIndex++] = edgeIndex;
        array[startIndex++] = x2;
        array[startIndex++] = y2;
        array[startIndex++] = color;
        array[startIndex++] = edgeIndex;
      }
    }, {
      key: "setUniforms",
      value: function setUniforms(params, _ref) {
        var gl = _ref.gl, uniformLocations = _ref.uniformLocations;
        var u_matrix = uniformLocations.u_matrix;
        gl.uniformMatrix3fv(u_matrix, false, params.matrix);
      }
    }]);
  })(EdgeProgram);
  var _WebGLRenderingContex2 = WebGLRenderingContext;
  var UNSIGNED_BYTE2 = _WebGLRenderingContex2.UNSIGNED_BYTE;
  var FLOAT2 = _WebGLRenderingContex2.FLOAT;

  // node_modules/sigma/utils/dist/sigma-utils.esm.js
  var import_is_graph3 = __toESM(require_is_graph());

  // node_modules/@sigma/node-border/dist/sigma-node-border.esm.js
  function _arrayWithHoles2(r) {
    if (Array.isArray(r)) return r;
  }
  function _iterableToArrayLimit2(r, l) {
    var t = null == r ? null : "undefined" != typeof Symbol && r[Symbol.iterator] || r["@@iterator"];
    if (null != t) {
      var e, n, i, u, a = [], f = true, o = false;
      try {
        if (i = (t = t.call(r)).next, 0 === l) {
          if (Object(t) !== t) return;
          f = false;
        } else for (; !(f = (e = i.call(t)).done) && (a.push(e.value), a.length !== l); f = true) ;
      } catch (r2) {
        o = true, n = r2;
      } finally {
        try {
          if (!f && null != t.return && (u = t.return(), Object(u) !== u)) return;
        } finally {
          if (o) throw n;
        }
      }
      return a;
    }
  }
  function _arrayLikeToArray2(r, a) {
    (null == a || a > r.length) && (a = r.length);
    for (var e = 0, n = Array(a); e < a; e++) n[e] = r[e];
    return n;
  }
  function _unsupportedIterableToArray2(r, a) {
    if (r) {
      if ("string" == typeof r) return _arrayLikeToArray2(r, a);
      var t = {}.toString.call(r).slice(8, -1);
      return "Object" === t && r.constructor && (t = r.constructor.name), "Map" === t || "Set" === t ? Array.from(r) : "Arguments" === t || /^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(t) ? _arrayLikeToArray2(r, a) : void 0;
    }
  }
  function _nonIterableRest2() {
    throw new TypeError("Invalid attempt to destructure non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method.");
  }
  function _slicedToArray2(r, e) {
    return _arrayWithHoles2(r) || _iterableToArrayLimit2(r, e) || _unsupportedIterableToArray2(r, e) || _nonIterableRest2();
  }
  function _classCallCheck2(a, n) {
    if (!(a instanceof n)) throw new TypeError("Cannot call a class as a function");
  }
  function _toPrimitive2(t, r) {
    if ("object" != typeof t || !t) return t;
    var e = t[Symbol.toPrimitive];
    if (void 0 !== e) {
      var i = e.call(t, r || "default");
      if ("object" != typeof i) return i;
      throw new TypeError("@@toPrimitive must return a primitive value.");
    }
    return ("string" === r ? String : Number)(t);
  }
  function _toPropertyKey2(t) {
    var i = _toPrimitive2(t, "string");
    return "symbol" == typeof i ? i : i + "";
  }
  function _defineProperties2(e, r) {
    for (var t = 0; t < r.length; t++) {
      var o = r[t];
      o.enumerable = o.enumerable || false, o.configurable = true, "value" in o && (o.writable = true), Object.defineProperty(e, _toPropertyKey2(o.key), o);
    }
  }
  function _createClass2(e, r, t) {
    return r && _defineProperties2(e.prototype, r), t && _defineProperties2(e, t), Object.defineProperty(e, "prototype", {
      writable: false
    }), e;
  }
  function _getPrototypeOf2(t) {
    return _getPrototypeOf2 = Object.setPrototypeOf ? Object.getPrototypeOf.bind() : function(t2) {
      return t2.__proto__ || Object.getPrototypeOf(t2);
    }, _getPrototypeOf2(t);
  }
  function _isNativeReflectConstruct2() {
    try {
      var t = !Boolean.prototype.valueOf.call(Reflect.construct(Boolean, [], function() {
      }));
    } catch (t2) {
    }
    return (_isNativeReflectConstruct2 = function() {
      return !!t;
    })();
  }
  function _assertThisInitialized2(e) {
    if (void 0 === e) throw new ReferenceError("this hasn't been initialised - super() hasn't been called");
    return e;
  }
  function _possibleConstructorReturn2(t, e) {
    if (e && ("object" == typeof e || "function" == typeof e)) return e;
    if (void 0 !== e) throw new TypeError("Derived constructors may only return object or undefined");
    return _assertThisInitialized2(t);
  }
  function _callSuper2(t, o, e) {
    return o = _getPrototypeOf2(o), _possibleConstructorReturn2(t, _isNativeReflectConstruct2() ? Reflect.construct(o, e || [], _getPrototypeOf2(t).constructor) : o.apply(t, e));
  }
  function _setPrototypeOf2(t, e) {
    return _setPrototypeOf2 = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
      return t2.__proto__ = e2, t2;
    }, _setPrototypeOf2(t, e);
  }
  function _inherits2(t, e) {
    if ("function" != typeof e && null !== e) throw new TypeError("Super expression must either be null or a function");
    t.prototype = Object.create(e && e.prototype, {
      constructor: {
        value: t,
        writable: true,
        configurable: true
      }
    }), Object.defineProperty(t, "prototype", {
      writable: false
    }), e && _setPrototypeOf2(t, e);
  }
  function _defineProperty2(e, r, t) {
    return (r = _toPropertyKey2(r)) in e ? Object.defineProperty(e, r, {
      value: t,
      enumerable: true,
      configurable: true,
      writable: true
    }) : e[r] = t, e;
  }
  function _arrayWithoutHoles2(r) {
    if (Array.isArray(r)) return _arrayLikeToArray2(r);
  }
  function _iterableToArray2(r) {
    if ("undefined" != typeof Symbol && null != r[Symbol.iterator] || null != r["@@iterator"]) return Array.from(r);
  }
  function _nonIterableSpread2() {
    throw new TypeError("Invalid attempt to spread non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method.");
  }
  function _toConsumableArray2(r) {
    return _arrayWithoutHoles2(r) || _iterableToArray2(r) || _unsupportedIterableToArray2(r) || _nonIterableSpread2();
  }
  function ownKeys2(e, r) {
    var t = Object.keys(e);
    if (Object.getOwnPropertySymbols) {
      var o = Object.getOwnPropertySymbols(e);
      r && (o = o.filter(function(r2) {
        return Object.getOwnPropertyDescriptor(e, r2).enumerable;
      })), t.push.apply(t, o);
    }
    return t;
  }
  function _objectSpread22(e) {
    for (var r = 1; r < arguments.length; r++) {
      var t = null != arguments[r] ? arguments[r] : {};
      r % 2 ? ownKeys2(Object(t), true).forEach(function(r2) {
        _defineProperty2(e, r2, t[r2]);
      }) : Object.getOwnPropertyDescriptors ? Object.defineProperties(e, Object.getOwnPropertyDescriptors(t)) : ownKeys2(Object(t)).forEach(function(r2) {
        Object.defineProperty(e, r2, Object.getOwnPropertyDescriptor(t, r2));
      });
    }
    return e;
  }
  var DEFAULT_BORDER_SIZE_MODE = "relative";
  var DEFAULT_CREATE_NODE_BORDER_OPTIONS = {
    drawLabel: void 0,
    drawHover: void 0,
    borders: [{
      size: {
        value: 0.1
      },
      color: {
        attribute: "borderColor"
      }
    }, {
      size: {
        fill: true
      },
      color: {
        attribute: "color"
      }
    }]
  };
  var DEFAULT_COLOR = "#000000";
  function getFragmentShader(_ref) {
    var borders = _ref.borders;
    var fillCounts = numberToGLSLFloat(borders.filter(function(_ref2) {
      var size = _ref2.size;
      return "fill" in size;
    }).length);
    var SHADER = (
      /*glsl*/
      "\nprecision highp float;\n\nvarying vec2 v_diffVector;\nvarying float v_radius;\n\n#ifdef PICKING_MODE\nvarying vec4 v_color;\n#else\n// For normal mode, we use the border colors defined in the program:\n".concat(borders.flatMap(function(_ref3, i) {
        var size = _ref3.size;
        return "attribute" in size ? ["varying float v_borderSize_".concat(i + 1, ";")] : [];
      }).join("\n"), "\n").concat(borders.flatMap(function(_ref4, i) {
        var color = _ref4.color;
        return "attribute" in color ? ["varying vec4 v_borderColor_".concat(i + 1, ";")] : "value" in color ? ["uniform vec4 u_borderColor_".concat(i + 1, ";")] : [];
      }).join("\n"), "\n#endif\n\nuniform float u_correctionRatio;\n\nconst float bias = 255.0 / 254.0;\nconst vec4 transparent = vec4(0.0, 0.0, 0.0, 0.0);\n\nvoid main(void) {\n  float dist = length(v_diffVector);\n  float aaBorder = 2.0 * u_correctionRatio;\n  float v_borderSize_0 = v_radius;\n  vec4 v_borderColor_0 = transparent;\n\n  // No antialiasing for picking mode:\n  #ifdef PICKING_MODE\n  if (dist > v_radius)\n    gl_FragColor = transparent;\n  else {\n    gl_FragColor = v_color;\n    gl_FragColor.a *= bias;\n  }\n  #else\n  // Sizes:\n").concat(borders.flatMap(function(_ref5, i) {
        var size = _ref5.size;
        if ("fill" in size) return [];
        size = size;
        var value = "attribute" in size ? "v_borderSize_".concat(i + 1) : numberToGLSLFloat(size.value);
        var factor = (size.mode || DEFAULT_BORDER_SIZE_MODE) === "pixels" ? "u_correctionRatio" : "v_radius";
        return ["  float borderSize_".concat(i + 1, " = ").concat(factor, " * ").concat(value, ";")];
      }).join("\n"), `
  // Now, let's split the remaining space between "fill" borders:
  float fillBorderSize = (v_radius - (`).concat(borders.flatMap(function(_ref6, i) {
        var size = _ref6.size;
        return !("fill" in size) ? ["borderSize_".concat(i + 1)] : [];
      }).join(" + "), ") ) / ").concat(fillCounts, ";\n").concat(borders.flatMap(function(_ref7, i) {
        var size = _ref7.size;
        return "fill" in size ? ["  float borderSize_".concat(i + 1, " = fillBorderSize;")] : [];
      }).join("\n"), "\n\n  // Finally, normalize all border sizes, to start from the full size and to end with the smallest:\n  float adjustedBorderSize_0 = v_radius;\n").concat(borders.map(function(_, i) {
        return "  float adjustedBorderSize_".concat(i + 1, " = adjustedBorderSize_").concat(i, " - borderSize_").concat(i + 1, ";");
      }).join("\n"), "\n\n  // Colors:\n  vec4 borderColor_0 = transparent;\n").concat(borders.map(function(_ref8, i) {
        var color = _ref8.color;
        var res = [];
        if ("attribute" in color) {
          res.push("  vec4 borderColor_".concat(i + 1, " = v_borderColor_").concat(i + 1, ";"));
        } else if ("transparent" in color) {
          res.push("  vec4 borderColor_".concat(i + 1, " = vec4(0.0, 0.0, 0.0, 0.0);"));
        } else {
          res.push("  vec4 borderColor_".concat(i + 1, " = u_borderColor_").concat(i + 1, ";"));
        }
        res.push("  borderColor_".concat(i + 1, ".a *= bias;"));
        res.push("  if (borderSize_".concat(i + 1, " <= 1.0 * u_correctionRatio) { borderColor_").concat(i + 1, " = borderColor_").concat(i, "; }"));
        return res.join("\n");
      }).join("\n"), "\n  if (dist > adjustedBorderSize_0) {\n    gl_FragColor = borderColor_0;\n  } else ").concat(borders.map(function(_, i) {
        return "if (dist > adjustedBorderSize_".concat(i, " - aaBorder) {\n    gl_FragColor = mix(borderColor_").concat(i + 1, ", borderColor_").concat(i, ", (dist - adjustedBorderSize_").concat(i, " + aaBorder) / aaBorder);\n  } else if (dist > adjustedBorderSize_").concat(i + 1, ") {\n    gl_FragColor = borderColor_").concat(i + 1, ";\n  } else ");
      }).join(""), " { /* Nothing to add here */ }\n  #endif\n}\n")
    );
    return SHADER;
  }
  function getVertexShader(_ref) {
    var borders = _ref.borders;
    var SHADER = (
      /*glsl*/
      "\nattribute vec2 a_position;\nattribute float a_size;\nattribute float a_angle;\n\nuniform mat3 u_matrix;\nuniform float u_sizeRatio;\nuniform float u_correctionRatio;\n\nvarying vec2 v_diffVector;\nvarying float v_radius;\n\n#ifdef PICKING_MODE\nattribute vec4 a_id;\nvarying vec4 v_color;\n#else\n".concat(borders.flatMap(function(_ref2, i) {
        var size = _ref2.size;
        return "attribute" in size ? ["attribute float a_borderSize_".concat(i + 1, ";"), "varying float v_borderSize_".concat(i + 1, ";")] : [];
      }).join("\n"), "\n").concat(borders.flatMap(function(_ref3, i) {
        var color = _ref3.color;
        return "attribute" in color ? ["attribute vec4 a_borderColor_".concat(i + 1, ";"), "varying vec4 v_borderColor_".concat(i + 1, ";")] : [];
      }).join("\n"), "\n#endif\n\nconst float bias = 255.0 / 254.0;\nconst vec4 transparent = vec4(0.0, 0.0, 0.0, 0.0);\n\nvoid main() {\n  float size = a_size * u_correctionRatio / u_sizeRatio * 4.0;\n  vec2 diffVector = size * vec2(cos(a_angle), sin(a_angle));\n  vec2 position = a_position + diffVector;\n  gl_Position = vec4(\n    (u_matrix * vec3(position, 1)).xy,\n    0,\n    1\n  );\n\n  v_radius = size / 2.0;\n  v_diffVector = diffVector;\n\n  #ifdef PICKING_MODE\n  v_color = a_id;\n  #else\n").concat(borders.flatMap(function(_ref4, i) {
        var size = _ref4.size;
        return "attribute" in size ? ["  v_borderSize_".concat(i + 1, " = a_borderSize_").concat(i + 1, ";")] : [];
      }).join("\n"), "\n").concat(borders.flatMap(function(_ref5, i) {
        var color = _ref5.color;
        return "attribute" in color ? ["  v_borderColor_".concat(i + 1, " = a_borderColor_").concat(i + 1, ";")] : [];
      }).join("\n"), "\n  #endif\n}\n")
    );
    return SHADER;
  }
  var _WebGLRenderingContex3 = WebGLRenderingContext;
  var UNSIGNED_BYTE3 = _WebGLRenderingContex3.UNSIGNED_BYTE;
  var FLOAT3 = _WebGLRenderingContex3.FLOAT;
  function createNodeBorderProgram(inputOptions) {
    var _NodeBorderProgram;
    var options = _objectSpread22(_objectSpread22({}, DEFAULT_CREATE_NODE_BORDER_OPTIONS), inputOptions || {});
    var borders = options.borders, drawLabel = options.drawLabel, drawHover = options.drawHover;
    var UNIFORMS2 = ["u_sizeRatio", "u_correctionRatio", "u_matrix"].concat(_toConsumableArray2(borders.flatMap(function(_ref, i) {
      var color = _ref.color;
      return "value" in color ? ["u_borderColor_".concat(i + 1)] : [];
    })));
    return _NodeBorderProgram = /* @__PURE__ */ (function(_NodeProgram) {
      _inherits2(NodeBorderProgram2, _NodeProgram);
      function NodeBorderProgram2() {
        var _this;
        _classCallCheck2(this, NodeBorderProgram2);
        for (var _len = arguments.length, args = new Array(_len), _key = 0; _key < _len; _key++) {
          args[_key] = arguments[_key];
        }
        _this = _callSuper2(this, NodeBorderProgram2, [].concat(args));
        _defineProperty2(_assertThisInitialized2(_this), "drawLabel", drawLabel);
        _defineProperty2(_assertThisInitialized2(_this), "drawHover", drawHover);
        return _this;
      }
      _createClass2(NodeBorderProgram2, [{
        key: "getDefinition",
        value: function getDefinition() {
          return {
            VERTICES: 3,
            VERTEX_SHADER_SOURCE: getVertexShader(options),
            FRAGMENT_SHADER_SOURCE: getFragmentShader(options),
            METHOD: WebGLRenderingContext.TRIANGLES,
            UNIFORMS: UNIFORMS2,
            ATTRIBUTES: [{
              name: "a_position",
              size: 2,
              type: FLOAT3
            }, {
              name: "a_id",
              size: 4,
              type: UNSIGNED_BYTE3,
              normalized: true
            }, {
              name: "a_size",
              size: 1,
              type: FLOAT3
            }].concat(_toConsumableArray2(borders.flatMap(function(_ref2, i) {
              var color = _ref2.color;
              return "attribute" in color ? [{
                name: "a_borderColor_".concat(i + 1),
                size: 4,
                type: UNSIGNED_BYTE3,
                normalized: true
              }] : [];
            })), _toConsumableArray2(borders.flatMap(function(_ref3, i) {
              var size = _ref3.size;
              return "attribute" in size ? [{
                name: "a_borderSize_".concat(i + 1),
                size: 1,
                type: FLOAT3
              }] : [];
            }))),
            CONSTANT_ATTRIBUTES: [{
              name: "a_angle",
              size: 1,
              type: FLOAT3
            }],
            CONSTANT_DATA: [[NodeBorderProgram2.ANGLE_1], [NodeBorderProgram2.ANGLE_2], [NodeBorderProgram2.ANGLE_3]]
          };
        }
      }, {
        key: "processVisibleItem",
        value: function processVisibleItem(nodeIndex, startIndex, data) {
          var array = this.array;
          array[startIndex++] = data.x;
          array[startIndex++] = data.y;
          array[startIndex++] = nodeIndex;
          array[startIndex++] = data.size;
          borders.forEach(function(_ref4) {
            var color = _ref4.color;
            if ("attribute" in color) array[startIndex++] = floatColor(data[color.attribute] || color.defaultValue || DEFAULT_COLOR);
          });
          borders.forEach(function(_ref5) {
            var size = _ref5.size;
            if ("attribute" in size) array[startIndex++] = data[size.attribute] || size.defaultValue;
          });
        }
      }, {
        key: "setUniforms",
        value: function setUniforms(params, _ref6) {
          var gl = _ref6.gl, uniformLocations = _ref6.uniformLocations;
          var u_sizeRatio = uniformLocations.u_sizeRatio, u_correctionRatio = uniformLocations.u_correctionRatio, u_matrix = uniformLocations.u_matrix;
          gl.uniform1f(u_correctionRatio, params.correctionRatio);
          gl.uniform1f(u_sizeRatio, params.sizeRatio);
          gl.uniformMatrix3fv(u_matrix, false, params.matrix);
          borders.forEach(function(_ref7, i) {
            var color = _ref7.color;
            if ("value" in color) {
              var location = uniformLocations["u_borderColor_".concat(i + 1)];
              var _colorToArray = colorToArray(color.value), _colorToArray2 = _slicedToArray2(_colorToArray, 4), r = _colorToArray2[0], g = _colorToArray2[1], b = _colorToArray2[2], a = _colorToArray2[3];
              gl.uniform4f(location, r / 255, g / 255, b / 255, a / 255);
            }
          });
        }
      }]);
      return NodeBorderProgram2;
    })(NodeProgram), _defineProperty2(_NodeBorderProgram, "ANGLE_1", 0), _defineProperty2(_NodeBorderProgram, "ANGLE_2", 2 * Math.PI / 3), _defineProperty2(_NodeBorderProgram, "ANGLE_3", 4 * Math.PI / 3), _NodeBorderProgram;
  }
  var NodeBorderProgram = createNodeBorderProgram();

  // src/views/graphRenderer.ts
  var import_graphology_layout_forceatlas2 = __toESM(require_graphology_layout_forceatlas2());
  var _EDGE_TOKENS = {
    co_mentioned_in: "--edge-co-mention",
    part_of: "--edge-part-of",
    has_company: "--edge-has-company",
    exposed_to: "--edge-exposed-to",
    belongs_to: "--edge-belongs-to",
    subsidiary_of: "--edge-subsidiary",
    jv_with: "--edge-jv",
    acquired: "--edge-acquired",
    competes_with: "--edge-competes",
    supplier_to: "--edge-supply",
    supplies_to: "--edge-supply",
    customer_of: "--edge-supply",
    same_group: "--edge-same-group",
    cited_in: "--edge-cited-in",
    semantic_peer: "--edge-semantic-peer",
    invested_in: "--edge-invested-in"
  };
  var EDGE_COLORS = {};
  (() => {
    const cs = getComputedStyle(document.documentElement);
    for (const [t, token] of Object.entries(_EDGE_TOKENS)) {
      const v = cs.getPropertyValue(token).trim();
      if (v) EDGE_COLORS[t] = v;
    }
  })();
  var edgeColor = (t) => t && EDGE_COLORS[t] || "#5C6E7E";
  var COMMUNITY_PALETTE = [
    "#E0A93E",
    "#2DD4BF",
    "#C39BFF",
    "#F5B14C",
    "#7CA8C9",
    "#F28B82",
    "#9BE08A",
    "#E79BE0",
    "#8AD7C6",
    "#D8C9A3"
  ];
  var _SYMMETRIC_RELS = /* @__PURE__ */ new Set(["co_mentioned_in", "jv_with", "competes_with", "same_group"]);
  var _NODE_GROUPS = {
    focal: { color: "#E0A93E", size: 16 },
    peer: { color: "#F5B14C", size: 12 },
    jv: { color: "#C39BFF", size: 12 },
    sibling: { color: "#B5838D", size: 12 },
    acquired: { color: "#F28B82", size: 12 },
    parent: { color: "#43AA8B", size: 12 },
    supplier: { color: "#7CA8C9", size: 12 },
    customer: { color: "#7CA8C9", size: 12 },
    outer: { color: "#66788C", size: 10 },
    company: { color: "#C7D3E0", size: 12 },
    theme: { color: "#C39BFF", size: 12 },
    edition: { color: "#D8C9A3", size: 15 },
    sector: { color: "#2DD4BF", size: 25 },
    "sector-focal": { color: "#1FB9A6", size: 28 },
    super_sector: { color: "#17766C", size: 27 },
    sub_sector: { color: "#3E8F86", size: 22 },
    member: { color: "#7CA8C9", size: 11 },
    "path-end": { color: "#E0A93E", size: 20 }
  };
  var _NODE_DEFAULT = { color: "#7E8FA3", size: 12 };
  var _EDGE_BASE = "rgba(96, 116, 140, 0.75)";
  var _CLOUD_EDGE = "rgba(96, 116, 140, 0.5)";
  function _scatterPosition(id) {
    let h = 2166136261;
    for (let i = 0; i < id.length; i++) {
      h ^= id.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    const angle = (h >>> 0) % 3600 / 3600 * 2 * Math.PI;
    const radius = 1500 + (h >>> 10) % 900;
    return { x: Math.round(Math.cos(angle) * radius), y: Math.round(Math.sin(angle) * radius) };
  }
  function _withAlpha(color, alpha) {
    const m = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(color);
    if (m) {
      const r = parseInt(m[1].slice(0, 2), 16);
      const g = parseInt(m[1].slice(2, 4), 16);
      const b = parseInt(m[1].slice(4, 6), 16);
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }
    const m2 = /^rgba?\(([^)]+)\)$/i.exec(color);
    if (m2) {
      const parts = m2[1].split(",").map((s) => parseFloat(s));
      return `rgba(${parts[0]}, ${parts[1]}, ${parts[2]}, ${alpha})`;
    }
    return color;
  }
  var _BorderProgram = createNodeBorderProgram({
    borders: [{ size: { value: 0.16 }, color: { attribute: "borderColor" } }],
    drawLabel: drawDiscNodeLabel,
    drawHover: drawDiscNodeHover
  });
  var GraphRenderer = class {
    constructor(container, callbacks) {
      // --- visual state (reducers read this; the cytoscape class system's
      //     declarative counterpart) ---------------------------------------- //
      this.hoverNode = null;
      this.hoverSet = null;
      this.component = null;
      this.focal = null;
      this.labelBucket = -1;
      this.communities = null;
      this.pathSet = null;
      this.container = container;
      this.callbacks = callbacks;
      window.__graphRenderer = this;
      this.graph = new import_graphology.default({ multi: true, type: "undirected" });
      this.sigma = new Sigma(this.graph, container, {
        nodeReducer: (node, data) => this._nodeReducer(node, data),
        edgeReducer: (edge, data) => this._edgeReducer(edge, data),
        nodeProgramClasses: { circle: NodeCircleProgram, border: _BorderProgram },
        edgeProgramClasses: { arrow: EdgeArrowProgram$1, line: EdgeLineProgram },
        defaultNodeType: "circle",
        defaultEdgeType: "arrow",
        defaultNodeColor: _NODE_DEFAULT.color,
        defaultEdgeColor: _EDGE_BASE,
        // Camera bounds = the old cytoscape zoom range, inverted:
        // sigma ratio shrinks as you zoom in (1/ratio ↔ cytoscape zoom).
        minCameraRatio: 1 / 3,
        maxCameraRatio: 5,
        labelFont: "'IBM Plex Mono', monospace",
        labelSize: 10,
        labelWeight: "500",
        labelColor: { color: "#DCE5EE" },
        edgeLabelFont: "'IBM Plex Mono', monospace",
        edgeLabelSize: 8,
        edgeLabelWeight: "400",
        edgeLabelColor: { color: "#B9C6D4" },
        labelDensity: 1,
        labelGridCellSize: 90,
        labelRenderedSizeThreshold: 6,
        renderLabels: true,
        renderEdgeLabels: true,
        enableEdgeEvents: true,
        // enterEdge/leaveEdge → edge tooltips
        zIndex: true
      });
      this.sigma.on("clickNode", ({ node }) => {
        this.callbacks.onNodeLeave();
        void Promise.resolve(this.callbacks.onNodeTap(node)).catch(
          (e) => this.callbacks.onError(e)
        );
      });
      this.sigma.on("clickStage", () => {
        this.callbacks.onNodeLeave();
        this.callbacks.onStageTap();
      });
      this.sigma.on("enterNode", ({ node, event }) => {
        this.hoverNode = node;
        this._setHover(node);
        this.callbacks.onNodeHover(this.nodeAttrs(node), event.x, event.y);
        this.sigma.refresh();
      });
      this.sigma.on("leaveNode", () => {
        this.hoverNode = null;
        this._setHover(null);
        this.callbacks.onNodeLeave();
        this.sigma.refresh();
      });
      this.sigma.on("enterEdge", ({ edge, event }) => {
        const a = this.graph.getEdgeAttributes(edge);
        this.callbacks.onEdgeHover(
          {
            ...a,
            source: this.graph.source(edge),
            target: this.graph.target(edge)
          },
          event.x,
          event.y
        );
      });
      this.sigma.on("leaveEdge", () => this.callbacks.onEdgeLeave());
      this.sigma.getCamera().on("updated", () => this.callbacks.onCameraChange());
    }
    // --- element sync ------------------------------------------------------ //
    /** Full rebuild: replace the canvas contents with `elements`. */
    setElements(elements) {
      this.graph.clear();
      const nodes = elements.filter((el) => !el.data.source);
      for (const el of nodes) {
        const group = el.data.group || "";
        const style = _NODE_GROUPS[group] || _NODE_DEFAULT;
        const cloud = el.data.cloud === "1";
        this.graph.addNode(el.data.id, {
          id: el.data.id,
          label: el.data.label || el.data.id,
          group,
          cloud,
          deg: el.data.deg,
          hub: el.data.hub === "1",
          component: el.data.component,
          centrality: el.data.centrality,
          // Cloud sizes arrive as px diameters; sigma wants the radius.
          size: cloud && el.data.size ? el.data.size / 2 : style.size,
          color: style.color,
          x: 0,
          y: 0
        });
      }
      this._seedRing();
      this._addEdges(elements);
      this.sigma.refresh();
    }
    /**
     * Progressive expansion: merge only the not-yet-present nodes/edges as
     * an "outer" ring seeded around `anchor`, then a short FA2 relaxation
     * that keeps existing positions (the old fcose-randomize:false role).
     * Returns the number of nodes added.
     */
    mergeElements(elements, anchor) {
      const seed = anchor && this.graph.hasNode(anchor) ? this.graph.getNodeAttributes(anchor) : { x: 0, y: 0 };
      let added = 0;
      const fresh = [];
      elements.forEach((el) => {
        if (el.data.source || this.graph.hasNode(el.data.id)) return;
        fresh.push(el);
      });
      fresh.forEach((el, i) => {
        const a = i / Math.max(1, fresh.length) * 2 * Math.PI;
        const r = 140 + 8 * Math.sqrt(i);
        const group = el.data.group || "outer";
        const style = _NODE_GROUPS[group] || _NODE_GROUPS.outer;
        this.graph.addNode(el.data.id, {
          id: el.data.id,
          label: el.data.label || el.data.id,
          group,
          cloud: false,
          centrality: el.data.centrality,
          size: style.size,
          color: style.color,
          x: seed.x + Math.cos(a) * r,
          y: seed.y + Math.sin(a) * r
        });
        added++;
      });
      this._addEdges(elements);
      if (added) this._fa2(80, 10);
      this.sigma.refresh();
      return added;
    }
    /** Add edges whose endpoints exist; cloud edges inherit the source
     *  node's component so set-highlighting keeps whole components lit. */
    _addEdges(elements) {
      elements.forEach((el) => {
        const s = el.data.source;
        const t = el.data.target;
        if (!s || !t || !this.graph.hasNode(s) || !this.graph.hasNode(t)) return;
        if (this.graph.hasEdge(el.data.id)) return;
        this.graph.addEdgeWithKey(el.data.id, s, t, {
          rel: el.data.type,
          label: el.data.label || null,
          cloud: el.data.cloud === "1",
          props: el.data.props,
          component: el.data.cloud === "1" ? this.graph.getNodeAttributes(s).component ?? void 0 : void 0
        });
      });
    }
    // --- visual state setters (each ends in a refresh) --------------------- //
    setFocal(id) {
      this.focal = id;
      this.sigma.refresh();
    }
    /** -1 = ungated (ego); 0/1/2 = zoom-fade buckets (cloud). */
    setLabelBucket(bucket) {
      if (bucket === this.labelBucket) return;
      this.labelBucket = bucket;
      this.sigma.setSetting("renderLabels", bucket !== 0);
      this.sigma.refresh();
    }
    setCommunities(map) {
      this.communities = map;
      this.sigma.refresh();
    }
    /** Highlight the connected set `component` (null clears). */
    highlightComponent(component) {
      this.component = component;
      this.sigma.refresh();
    }
    /** Highlight a shortest-path chain (null clears); edges between
     *  consecutive chain nodes light up, everything else fades. */
    highlightPath(chain) {
      if (!chain) {
        this.pathSet = null;
        this.sigma.refresh();
        return;
      }
      const nodes = new Set(chain);
      const edges = /* @__PURE__ */ new Set();
      for (let i = 0; i < chain.length - 1; i++) {
        this.graph.edges(chain[i], chain[i + 1]).forEach((k) => edges.add(k));
      }
      this.pathSet = { nodes, edges };
      this.sigma.refresh();
    }
    // --- camera ------------------------------------------------------------- //
    /** Current zoom in "cytoscape" units (1/ratio) — the slider's currency. */
    zoomValue() {
      return 1 / this.sigma.getCamera().ratio;
    }
    setZoomValue(z) {
      const cam = this.sigma.getCamera();
      cam.setState({ ...cam.getState(), ratio: Math.min(5, Math.max(1 / 3, 1 / z)) });
    }
    zoomIn() {
      void this.sigma.getCamera().animatedZoom({ factor: 1.25, duration: 180 });
    }
    zoomOut() {
      void this.sigma.getCamera().animatedUnzoom({ factor: 1.25, duration: 180 });
    }
    /**
     * Fit the whole graph into the viewport with `padding` px margin, then
     * floor the zoom-in at `maxZoom` (slider units: 1.3 = 130%). Sigma's
     * normalized camera keeps node sizes screen-constant at fit, so the old
     * cytoscape small-graph balloon problem (and its zoom cap machinery)
     * does not arise; the cap remains as a sanity bound.
     */
    fitCapped(padding = 30, maxZoom = 3, focusId) {
      if (!this.graph.order) return;
      const w = this.container.clientWidth || 800;
      const h = this.container.clientHeight || 600;
      const larger = Math.max(w, h);
      const fitRatio = larger / Math.max(1, larger - 2 * padding);
      const ratio = Math.max(fitRatio, 1 / maxZoom);
      const cam = this.sigma.getCamera();
      cam.setState({ ...cam.getState(), x: 0.5, y: 0.5, angle: 0, ratio });
      if (focusId && this.graph.hasNode(focusId)) {
        const pos = this.sigma.getNodeDisplayData(focusId);
        if (pos) cam.setState({ ...cam.getState(), x: pos.x, y: pos.y });
      }
    }
    /**
     * All-mode search spotlight: animate the camera onto `id`'s closed
     * neighbourhood. Returns the neighbourhood node count (status line).
     */
    spotlight(id) {
      if (!this.graph.hasNode(id)) return 0;
      const nodes = /* @__PURE__ */ new Set([id, ...this.graph.neighbors(id)]);
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      nodes.forEach((n) => {
        const pos = this.sigma.getNodeDisplayData(n);
        if (!pos) return;
        minX = Math.min(minX, pos.x);
        minY = Math.min(minY, pos.y);
        maxX = Math.max(maxX, pos.x);
        maxY = Math.max(maxY, pos.y);
      });
      if (Number.isFinite(minX)) {
        const cam = this.sigma.getCamera();
        const span = Math.max(maxX - minX, maxY - minY) || 0.1;
        void cam.animate(
          {
            ...cam.getState(),
            x: (minX + maxX) / 2,
            y: (minY + maxY) / 2,
            ratio: Math.min(5, span * 1.3)
          },
          { duration: 280 }
        );
      }
      return nodes.size;
    }
    // --- queries ------------------------------------------------------------ //
    nodeCount() {
      return this.graph.order;
    }
    hasNode(id) {
      return this.graph.hasNode(id);
    }
    nodeAttrs(id) {
      return this.graph.getNodeAttributes(id);
    }
    refresh() {
      this.sigma.refresh();
    }
    // --- reducers (the declarative stylesheet) ------------------------------ //
    _nodeReducer(node, data) {
      const display = {
        x: data.x,
        y: data.y,
        label: data.label,
        size: data.size,
        color: data.color,
        type: "circle",
        zIndex: 0
      };
      if (this.communities) {
        const c = this.communities.get(node);
        if (c !== void 0) display.color = COMMUNITY_PALETTE[c % COMMUNITY_PALETTE.length];
      }
      if (data.cloud && this.labelBucket === 1 && !data.hub) display.label = null;
      const faded = this.hoverSet && !this.hoverSet.nodes.has(node) || this.component !== null && data.component !== this.component || this.pathSet && !this.pathSet.nodes.has(node);
      if (faded) {
        display.color = _withAlpha(display.color || _NODE_DEFAULT.color, 0.22);
        display.label = null;
      }
      if (this.component !== null && data.component === this.component) {
        display.type = "border";
        display.borderColor = "#E0A93E";
        display.zIndex = 6;
      }
      if (this.pathSet && this.pathSet.nodes.has(node)) {
        display.type = "border";
        display.borderColor = "#E0A93E";
        display.zIndex = 7;
      }
      if (this.focal === node) {
        display.type = "border";
        display.borderColor = "#F5D08C";
        display.size = Math.max(display.size || 0, 14) * 1.1;
        display.zIndex = 8;
      }
      if (this.hoverNode === node) {
        display.highlighted = true;
        display.forceLabel = true;
      }
      return display;
    }
    _edgeReducer(edge, data) {
      const base = data.cloud ? _CLOUD_EDGE : edgeColor(data.rel);
      const display = {
        label: data.cloud ? null : data.label || null,
        size: data.cloud ? 0.7 : 1.6,
        color: base,
        type: !data.cloud && data.rel && !_SYMMETRIC_RELS.has(data.rel) ? "arrow" : "line",
        zIndex: 0
      };
      if (data.rel === "path-hop") {
        display.color = "#E0A93E";
        display.size = 3.5;
        display.zIndex = 10;
      }
      const faded = this.hoverSet && !this.hoverSet.edges.has(edge) || this.component !== null && data.component !== this.component || this.pathSet && !this.pathSet.edges.has(edge);
      if (faded) display.color = _withAlpha(display.color || base, 0.15);
      if (this.pathSet && this.pathSet.edges.has(edge)) {
        display.color = "#E0A93E";
        display.size = 3;
        display.zIndex = 9;
      }
      if (this.component !== null && data.component === this.component) {
        display.color = _withAlpha(base, 0.9);
        display.size = 1.6;
      }
      return display;
    }
    /** Closed neighbourhood of `id` (nodes + internal edges) for hover dim. */
    _setHover(id) {
      if (!id) {
        this.hoverSet = null;
        return;
      }
      const nodes = /* @__PURE__ */ new Set([id]);
      this.graph.forEachNeighbor(id, (n) => nodes.add(n));
      const edges = /* @__PURE__ */ new Set();
      this.graph.forEachEdge((k, _attrs, s, t) => {
        if (nodes.has(s) && nodes.has(t)) edges.add(k);
      });
      this.hoverSet = { nodes, edges };
    }
    // --- layouts ------------------------------------------------------------ //
    /**
     * Run layout `name` over the current graph. "cached" falls through to
     * "components" when the sidecar is cold; anything unknown → FA2.
     */
    runLayout(name, opts = {}) {
      if (!this.graph.order) return;
      const randomize = opts.randomize !== false;
      if (name === "cached") {
        const cached = opts.cachedPositions;
        if (cached) {
          this.graph.forEachNode((id) => {
            const p = cached[id] ?? _scatterPosition(id);
            this.graph.setNodeAttribute(id, "x", p.x);
            this.graph.setNodeAttribute(id, "y", p.y);
          });
          this.sigma.refresh();
          return;
        }
        name = "components";
      }
      if (name === "components") {
        const positions = this._componentPositions();
        if (positions) {
          this.graph.forEachNode((id) => {
            const p = positions[id];
            if (p) {
              this.graph.setNodeAttribute(id, "x", p.x);
              this.graph.setNodeAttribute(id, "y", p.y);
            }
          });
          this.sigma.refresh();
          return;
        }
        name = "concentric";
      }
      switch (name) {
        case "concentric":
          this._concentricPositions();
          break;
        case "circle":
          this._circlePositions();
          break;
        case "grid":
          this._gridPositions();
          break;
        case "breadthfirst":
          this._breadthFirstPositions(opts.root ?? null);
          break;
        case "cose":
          if (randomize) this._seedRing();
          this._fa2(opts.cloud ? 150 : 250, 6);
          break;
        default:
          if (randomize) this._seedRing();
          this._fa2(opts.cloud ? 600 : 500, 10);
          break;
      }
      this.sigma.refresh();
    }
    /** Deterministic circular seed + FNV jitter (FA2 needs a non-degenerate
     *  start; identical input → identical layout across visits). */
    _seedRing() {
      const n = this.graph.order;
      if (!n) return;
      const radius = Math.max(60, Math.sqrt(n) * 40);
      let i = 0;
      this.graph.forEachNode((id) => {
        const base = 2 * Math.PI * i++ / n;
        let h = 2166136261;
        for (let c = 0; c < id.length; c++) {
          h ^= id.charCodeAt(c);
          h = Math.imul(h, 16777619);
        }
        const jitter = ((h >>> 0) % 1e3 / 1e3 - 0.5) * 0.04;
        this.graph.setNodeAttribute(id, "x", Math.cos(base + jitter) * radius);
        this.graph.setNodeAttribute(id, "y", Math.sin(base + jitter) * radius);
      });
    }
    _fa2(iterations, scalingRatio) {
      import_graphology_layout_forceatlas2.default.assign(this.graph, {
        iterations,
        settings: {
          barnesHutOptimize: this.graph.order > 500,
          barnesHutTheta: 0.5,
          gravity: 1,
          scalingRatio,
          slowDown: 2,
          adjustSizes: true,
          edgeWeightInfluence: 0,
          strongGravityMode: false,
          linLogMode: false,
          outboundAttractionDistribution: false
        }
      });
    }
    /** Component-separating cloud layout: each connected set grid-packed
     *  with its hub at the cell centre (ported verbatim from graph.ts). */
    _componentPositions() {
      const compOf = /* @__PURE__ */ new Map();
      let has = false;
      this.graph.forEachNode((id, a) => {
        const c = a.component;
        compOf.set(id, c || id);
        if (c) has = true;
      });
      if (!has) return null;
      const degree = {};
      this.graph.forEachEdge((_k, _a, s, t) => {
        degree[s] = (degree[s] || 0) + 1;
        degree[t] = (degree[t] || 0) + 1;
      });
      const compMap = /* @__PURE__ */ new Map();
      this.graph.forEachNode((id) => {
        const c = compOf.get(id);
        if (!compMap.has(c)) compMap.set(c, []);
        compMap.get(c).push(id);
      });
      const comps = [...compMap.values()].sort((a, b) => b.length - a.length);
      const nodeSpacing = 52;
      const maxComp = Math.max(...comps.map((c) => c.length));
      const cellPad = maxComp >= 8 ? 90 : 70;
      const cellRadius = (n) => Math.max(30, Math.sqrt(n) * nodeSpacing / 2);
      const maxR = Math.max(...comps.map((c) => cellRadius(c.length)));
      const cell = maxR * 2 + cellPad;
      const cols = Math.max(1, Math.ceil(Math.sqrt(comps.length)));
      const positions = {};
      comps.forEach((comp, i) => {
        const cx = i % cols * cell + cell / 2;
        const cyy = Math.floor(i / cols) * cell + cell / 2;
        const r = cellRadius(comp.length);
        const sorted = [...comp].sort((a, b) => (degree[b] || 0) - (degree[a] || 0));
        const hub = sorted[0];
        if (comp.length === 2) {
          positions[hub] = { x: cx, y: cyy - r };
          positions[sorted[1]] = { x: cx, y: cyy + r };
          return;
        }
        positions[hub] = { x: cx, y: cyy };
        sorted.slice(1).forEach((id, j) => {
          const ang = -Math.PI / 2 + j / (sorted.length - 1) * Math.PI * 2;
          positions[id] = { x: cx + Math.cos(ang) * r, y: cyy + Math.sin(ang) * r };
        });
      });
      return positions;
    }
    /** Concentric rings by centrality (one ring per distinct value). */
    _concentricPositions() {
      const nodes = this.graph.nodes();
      const ranked = [...nodes].sort(
        (a, b) => (this.graph.getNodeAttributes(b).centrality ?? 0) - (this.graph.getNodeAttributes(a).centrality ?? 0)
      );
      const levels = [];
      ranked.forEach((id) => {
        const v = this.graph.getNodeAttributes(id).centrality ?? 0;
        const last = levels[levels.length - 1];
        if (last && last.value === v) last.members.push(id);
        else levels.push({ value: v, members: [id] });
      });
      levels.forEach((level, li) => {
        const r = 70 + li * 90;
        level.members.forEach((id, i) => {
          const a = 2 * Math.PI * i / level.members.length + li * 0.5;
          this.graph.setNodeAttribute(id, "x", Math.cos(a) * r);
          this.graph.setNodeAttribute(id, "y", Math.sin(a) * r);
        });
      });
    }
    /** Single ring in insertion order. */
    _circlePositions() {
      const nodes = this.graph.nodes();
      const r = 40 + 10 * Math.sqrt(nodes.length);
      nodes.forEach((id, i) => {
        const a = 2 * Math.PI * i / nodes.length;
        this.graph.setNodeAttribute(id, "x", Math.cos(a) * r);
        this.graph.setNodeAttribute(id, "y", Math.sin(a) * r);
      });
    }
    /** Deterministic grid, id-sorted. */
    _gridPositions() {
      const nodes = this.graph.nodes().sort();
      const cols = Math.max(1, Math.ceil(Math.sqrt(nodes.length)));
      nodes.forEach((id, i) => {
        this.graph.setNodeAttribute(id, "x", i % cols * 80);
        this.graph.setNodeAttribute(id, "y", Math.floor(i / cols) * 80);
      });
    }
    /** BFS layering from `root` (path chains lay out as a straight line). */
    _breadthFirstPositions(root) {
      const start = root && this.graph.hasNode(root) ? root : this.graph.nodes()[0];
      const seen = /* @__PURE__ */ new Set([start]);
      const layers = [[start]];
      let frontier = [start];
      while (frontier.length) {
        const next = [];
        frontier.forEach((n) => {
          this.graph.forEachNeighbor(n, (m) => {
            if (!seen.has(m)) {
              seen.add(m);
              next.push(m);
            }
          });
        });
        if (next.length) layers.push(next);
        frontier = next;
      }
      this.graph.forEachNode((id) => {
        if (!seen.has(id)) {
          seen.add(id);
          layers[layers.length - 1].push(id);
        }
      });
      layers.forEach((layer, li) => {
        layer.forEach((id, i) => {
          this.graph.setNodeAttribute(id, "x", (i - (layer.length - 1) / 2) * 130);
          this.graph.setNodeAttribute(id, "y", li * 140);
        });
      });
    }
  };

  // src/views/graph.ts
  var _HUB_DEGREE = 6;
  var _EXPAND_NODE_CAP = 150;
  var _SECTOR_RENDER_CAP = 200;
  var _RATIO_LBL_OFF = 2.5;
  var _RATIO_LBL_HUBS = 1.4;
  var _NODE_SIZE_MIN = 9;
  var _NODE_SIZE_MAX = 30;
  var _EGO_FIT_MAX_ZOOM = 1.3;
  var _CLOUD_FIT_MAX_ZOOM = 1.1;
  var _LABEL_ALWAYS_NODES = 400;
  var _EDGE_LABEL_EDGES = 80;
  var METRIC_BLURBS = {
    degree_centrality: "most-connected entities by raw edge count",
    pagerank: "influence propagated through the whole graph",
    betweenness_centrality: "brokers sitting on the most shortest paths",
    closeness_centrality: "entities closest to everyone else",
    eigenvector_centrality: "connected to the well-connected",
    harmonic_centrality: "closeness that tolerates unreachable pockets",
    katz_centrality: "influence damped by path length",
    laplacian_centrality: "structural importance via Laplacian energy",
    local_reaching_centrality: "reach over each neighbour's own ties",
    local_clustering_coefficient: "how densely each entity's neighbours interconnect",
    link_prediction: "predicted partners per entity (persisted scoring run)",
    voterank: "VoteRank seed set \u2014 the nodes worth starting a story from"
  };
  var _NON_EVENT_GROUPS = /* @__PURE__ */ new Set([
    "sector",
    "sector-focal",
    "member",
    "super_sector",
    "sub_sector",
    "edition",
    "theme"
  ]);
  var GraphView = class {
    constructor() {
      // --- graph-tab state (lazy-initialized in loadGraphView) -------------- //
      this.graph = null;
    }
    async loadGraphView() {
      if (!this.graph) {
        this.graph = {
          renderer: null,
          central: null,
          elements: null,
          entitiesLoaded: false,
          mode: "ego",
          cloud: null,
          cloudMode: false,
          hiddenEdgeTypes: /* @__PURE__ */ new Set(),
          labelBucket: -1,
          labelAlways: false,
          layoutTouched: false,
          focalBundle: null,
          rankData: /* @__PURE__ */ new Map(),
          rankGroups: null,
          rankSeeds: null,
          suggestions: /* @__PURE__ */ new Map(),
          timeByYear: null,
          timeBridges: null,
          timeCoMentions: null,
          nearDup: null,
          detailSeq: 0,
          lastNodeTapAt: 0
        };
      }
      if (!this.graph.renderer) {
        const canvas = getEl("graph-canvas");
        this.graph.renderer = new GraphRenderer(canvas, {
          // Node tap: cloud mode highlights the tapped connected set +
          // opens its detail panel; ego mode re-centres on the node.
          // Double-fire guard: sigma emits two clickNode events ahead
          // of a double click — the ego re-centre (a fetch + full
          // re-render) only runs 350 ms after the previous tap.
          onNodeTap: (id) => {
            const g = this.graph;
            const attrs = g?.renderer?.nodeAttrs(id);
            if (!g || !attrs) return;
            if (g.mode === "all") {
              this._highlightCloudSet(attrs);
              this._renderGraphDetail(attrs);
              return;
            }
            const now = performance.now();
            if (now - g.lastNodeTapAt < 350) return;
            g.lastNodeTapAt = now;
            if (id !== g.central) {
              getEl("graph-search").value = id;
              this._setMode("ego");
              return this.loadEgoNetwork(id);
            }
          },
          onStageTap: () => {
            if (this.graph?.mode === "all") this._clearCloudHighlight();
          },
          onNodeHover: (attrs, x, y) => this._showNodeTip(attrs, x, y),
          onNodeLeave: () => this._hideTip(),
          onEdgeHover: (attrs, x, y) => this._showEdgeTip(attrs, x, y),
          onEdgeLeave: () => this._hideTip(),
          onCameraChange: () => this._onCameraChange(),
          onError: (e) => this._setGraphStatus(`graph error: ${e.message}`)
        });
        const centreFromSearch = async () => {
          const name = getEl("graph-search").value.trim();
          if (!name) return;
          if (this.graph && this.graph.mode === "all" && this.graph.cloud && this._spotlightInCloud(name))
            return;
          this._setMode("ego");
          await this.loadEgoNetwork(name);
        };
        getEl("graph-search-btn").addEventListener("click", () => void centreFromSearch());
        getEl("graph-search").addEventListener("keydown", (e) => {
          if (e.key === "Enter") void centreFromSearch();
        });
        getEl("graph-layout").addEventListener("change", (e) => {
          if (!this.graph || !this.graph.renderer) return;
          this.graph.layoutTouched = true;
          const inCloud = this.graph.mode === "all";
          this._runGraphLayout(e.target.value, inCloud);
          if (inCloud) this._fitCapped(30, _CLOUD_FIT_MAX_ZOOM);
        });
        getEl("graph-filter").addEventListener("change", () => {
          this._setMode("ego");
          if (this.graph.central) this.loadEgoNetwork(this.graph.central);
        });
        getEl("graph-refresh-db").addEventListener("click", async () => {
          const btn = getEl("graph-refresh-db");
          btn.disabled = true;
          try {
            const data = await postJson("/api/graph/refresh");
            if (data.status !== "ok") {
              this._setGraphStatus("refresh failed");
              return;
            }
            this._setGraphStatus("DB refreshed \u2014 view reloaded");
            const rerunMode = this.graph.mode;
            const rerunCentral = this.graph.central;
            this.graph.elements = null;
            this.graph.central = null;
            this.graph.cloud = null;
            this.graph.rankData.clear();
            this.graph.rankGroups = null;
            this.graph.rankSeeds = null;
            this.graph.suggestions.clear();
            this.graph.timeByYear = null;
            this.graph.timeBridges = null;
            this.graph.timeCoMentions = null;
            this.graph.nearDup = null;
            if (rerunMode === "all") {
              void this.loadGraphCloud();
            } else if (rerunMode === "ego" && rerunCentral) {
              void this.loadEgoNetwork(rerunCentral);
            } else if (rerunMode === "rank") {
              void this._loadRankView();
            } else if (rerunMode === "time") {
              void this._loadTimeView();
            }
          } catch (e) {
            this._setGraphStatus("refresh failed: " + e.message);
          } finally {
            btn.disabled = false;
          }
        });
        getEl("shortest-btn").addEventListener("click", () => this.loadShortestPath());
        getEl("shortest-clear").addEventListener("click", () => this.clearShortestPath());
        this._initLensRail();
        this._initGraphZoom();
        this._setMode("ego");
      }
      if (!this.graph.entitiesLoaded) {
        await this.loadGraphEntityList();
        this.graph.entitiesLoaded = true;
      }
      setTimeout(() => this.graph?.renderer?.refresh(), 50);
    }
    // --- Lens rail: modes + chronoscope + cloud filters ------------------- //
    /** Wire the mode buttons, the As-Of chronoscope, and the cloud toggles. */
    _initLensRail() {
      document.querySelectorAll(".lens-mode").forEach((btn) => {
        btn.addEventListener("click", () => {
          const mode = btn.dataset.lensMode;
          if (this.graph && this.graph.mode !== mode) this._setMode(mode);
        });
      });
      const slider = getEl("chronoscope");
      const year = getEl("chronoscope-year");
      const reset = getEl("chronoscope-reset");
      const describe = () => slider.value === slider.max ? "now" : `as of ${slider.value}`;
      const rerun = () => {
        if (!this.graph) return;
        if (this.graph.mode === "ego" && this.graph.central) {
          this.loadEgoNetwork(this.graph.central);
        } else if (this.graph.mode === "path") {
          const a = getEl("shortest-a").value.trim();
          const b = getEl("shortest-b").value.trim();
          if (a && b) this.loadShortestPath();
        }
      };
      slider.addEventListener("input", () => {
        year.textContent = describe();
        year.classList.toggle("armed", slider.value !== slider.max);
        rerun();
      });
      reset.addEventListener("click", () => {
        slider.value = slider.max;
        year.textContent = "now";
        year.classList.remove("armed");
        rerun();
      });
      getEl("cloud-min-degree").addEventListener("change", () => this._applyCloudFilter());
      getEl("cloud-everything").addEventListener("change", () => this._applyCloudFilter());
      getEl("cloud-community").addEventListener("change", (e) => {
        this._applyCommunityShading(e.target.checked);
      });
      getEl("rank-metric").addEventListener("change", () => void this._loadRankTable());
      getEl("rank-top").addEventListener("change", () => void this._loadRankTable());
      getEl("suggest-method").addEventListener("change", () => void this._loadSuggestions());
      getEl("neardup-run").addEventListener("click", () => void this._loadNearDuplicates());
    }
    /** Current temporal filter ("" = now / no filter). */
    _asOf() {
      const slider = document.getElementById("chronoscope");
      if (!slider || slider.value === slider.max) return "";
      return slider.value;
    }
    /** Switch lens mode; toggles panels and (re)loads data as needed. */
    _setMode(mode) {
      if (!this.graph) return;
      this.graph.mode = mode;
      this.graph.cloudMode = mode === "all";
      const view = getEl("graph-view");
      view.dataset.lensMode = mode;
      document.querySelectorAll(".lens-mode").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.lensMode === mode);
        btn.setAttribute("aria-pressed", btn.dataset.lensMode === mode ? "true" : "false");
      });
      getEl("lens-cloud-controls").style.display = mode === "all" ? "block" : "none";
      getEl("graph-cloud-panel").style.display = mode === "all" ? "block" : "none";
      getEl("graph-shortest").style.display = mode === "path" ? "block" : "none";
      getEl("graph-filter").closest(".filters")?.style.setProperty(
        "display",
        mode === "ego" ? "" : "none"
      );
      const tableMode = mode === "rank" || mode === "time";
      getEl("lens-rank").style.display = mode === "rank" ? "block" : "none";
      getEl("lens-time").style.display = mode === "time" ? "block" : "none";
      getEl("lens-asof-block").style.display = tableMode ? "none" : "block";
      document.querySelector("#graph-view .graph-layout-row")?.style.setProperty("display", tableMode ? "none" : "");
      const empty = getEl("graph-empty");
      empty.style.display = mode === "ego" && !this.graph.central ? "flex" : "none";
      if (mode === "all") {
        if (this.graph.cloud) {
          this._applyCloudFilter();
          this._renderCloudLegend(this.graph.cloud.data);
          this._renderRelationshipCloud(this.graph.cloud.data.relationship_types);
        } else {
          this.loadGraphCloud();
        }
      }
      if (mode === "path") {
        this._setGraphStatus("Path \u2014 enter two entities below");
      }
      if (mode === "rank") void this._loadRankView();
      if (mode === "time") void this._loadTimeView();
      if (!tableMode) setTimeout(() => this.graph?.renderer?.refresh(), 0);
    }
    async loadGraphEntityList() {
      try {
        const dl = getEl("graph-entities-list");
        const parts = [];
        const dc = await fetchJson("/api/entities?type=company&limit=3000");
        (dc.entities || []).forEach((e) => {
          parts.push(`<option value="${e.name}">${e.name}</option>`);
        });
        const ds = await fetchJson("/api/entities?type=sector&limit=500");
        (ds.entities || []).forEach((e) => {
          parts.push(`<option value="${e.name}">${e.name} (sector)</option>`);
        });
        dl.innerHTML = parts.join("");
      } catch (e) {
        console.warn("graph typeahead load failed", e);
      }
    }
    // --- All mode: the whole graph, filtered ------------------------------ //
    /** Fetch + cache the whole graph, then render through the active filters. */
    async loadGraphCloud() {
      if (!this.graph || !this.graph.renderer) return;
      this._setGraphStatus("Loading full graph...");
      let data;
      try {
        data = await fetchJson("/api/graph/cloud");
      } catch (e) {
        this._setGraphStatus(`Error: ${e.message}`);
        return;
      }
      const degree = {};
      data.edges.forEach((e) => {
        degree[e.source] = (degree[e.source] || 0) + 1;
        degree[e.target] = (degree[e.target] || 0) + 1;
      });
      this.graph.cloud = { data, degree, communities: null, positions: null };
      this.graph.central = null;
      this.graph.entityType = void 0;
      this._clearCloudHighlight();
      void this._loadCachedPositions();
      this._setGraphStatus("Building the cloud...");
      try {
        this._applyCloudFilter();
        this._renderCloudLegend(data);
        this._renderRelationshipCloud(data.relationship_types);
      } catch (e) {
        this._setGraphStatus(`Cloud render failed: ${e.message}`);
      }
    }
    /** Fetch the server-side layout sidecar into the cloud cache and apply
     * it to the visible cloud when the user hasn't picked a layout yet.
     * Silent on failure by design: positions are an enhancement, the local
     * components/concentric default remains fully functional without them. */
    async _loadCachedPositions() {
      try {
        const data = await fetchJson("/api/graph/positions");
        const graph = this.graph;
        if (!data.positions || !graph?.cloud) return;
        const positions = {};
        for (const [id, xy] of Object.entries(data.positions)) {
          positions[id] = { x: xy[0], y: xy[1] };
        }
        graph.cloud.positions = positions;
        if (graph.mode === "all" && graph.renderer && !graph.layoutTouched && getEl("graph-layout").value === "fcose") {
          this._runGraphLayout("cached", true);
          this._fitCapped(30, _CLOUD_FIT_MAX_ZOOM);
          this._applyLabelBucket(this._labelBucketFor(graph.renderer.zoomValue()));
        }
      } catch {
      }
    }
    /**
     * Rebuild the cloud canvas from the cached response honouring the rail
     * toggles (min-degree ≥ 2 default, "everything" opt-out) and the legend's
     * edge-type chips. Client-side only — no refetch.
     *
     * Edge-filter semantics: while ANY relationship type is toggled off, the
     * canvas shows the subgraph INDUCED by the visible edges (their endpoint
     * nodes — the min-degree rail toggle is suspended for that view), and it
     * re-lays-out + fits so the subgraph is actually legible. With every
     * type visible, the min-degree behaviour is unchanged.
     */
    _applyCloudFilter() {
      const renderer = this.graph && this.graph.renderer;
      const cache = this.graph && this.graph.cloud;
      if (!renderer || !cache) return;
      const everything = getEl("cloud-everything").checked;
      const minDegree = everything ? 1 : getEl("cloud-min-degree").checked ? 2 : 1;
      const hidden = this.graph.hiddenEdgeTypes;
      const edgeFilterActive = cache.data.relationship_types.some(
        (rt) => hidden.has(rt.edge_type)
      );
      let kept;
      let visibleEdges;
      if (edgeFilterActive) {
        visibleEdges = cache.data.edges.filter((e) => !hidden.has(e.edge_type));
        kept = /* @__PURE__ */ new Set();
        visibleEdges.forEach((e) => {
          kept.add(e.source);
          kept.add(e.target);
        });
      } else {
        visibleEdges = cache.data.edges;
        kept = new Set(
          cache.data.nodes.filter((n) => (cache.degree[n.id] || 0) >= minDegree).map((n) => n.id)
        );
      }
      const parent = /* @__PURE__ */ new Map();
      const find = (x) => {
        let root = x;
        while (parent.get(root) !== root) root = parent.get(root) || root;
        let cur = x;
        while (cur !== root) {
          const next = parent.get(cur) || root;
          parent.set(cur, root);
          cur = next;
        }
        return root;
      };
      kept.forEach((id) => parent.set(id, id));
      visibleEdges.forEach((e) => {
        if (!kept.has(e.source) || !kept.has(e.target)) return;
        const ra = find(e.source);
        const rb = find(e.target);
        if (ra !== rb) parent.set(ra, rb);
      });
      const visDegree = {};
      visibleEdges.forEach((e) => {
        visDegree[e.source] = (visDegree[e.source] || 0) + 1;
        visDegree[e.target] = (visDegree[e.target] || 0) + 1;
      });
      let maxDeg = 1;
      kept.forEach((id) => {
        maxDeg = Math.max(maxDeg, visDegree[id] || 0);
      });
      const sizeFor = (deg) => Math.round(
        _NODE_SIZE_MIN + (_NODE_SIZE_MAX - _NODE_SIZE_MIN) * Math.sqrt(deg / maxDeg)
      );
      const elements = cache.data.nodes.filter((n) => kept.has(n.id)).map((n) => {
        const deg = visDegree[n.id] || 0;
        return {
          data: {
            id: n.id,
            label: n.label,
            group: n.entity_type,
            cloud: "1",
            deg,
            size: sizeFor(deg),
            centrality: deg,
            hub: deg >= _HUB_DEGREE ? "1" : "",
            component: find(n.id)
          }
        };
      });
      const labelEdges = visibleEdges.length <= _EDGE_LABEL_EDGES && cache.data.relationship_types.length - hidden.size > 1;
      visibleEdges.forEach((e) => {
        if (!kept.has(e.source) || !kept.has(e.target)) return;
        elements.push({
          data: {
            id: `${e.source}__${e.target}__${e.edge_type}`,
            source: e.source,
            target: e.target,
            type: e.edge_type,
            label: labelEdges ? e.edge_type : "",
            // 4k+ labels are the #1 render cost
            cloud: "1"
          }
        });
      });
      renderer.setElements(elements);
      this.graph.elements = elements;
      const nodeCount = elements.filter((e) => !e.data.source).length;
      const edgeCount = elements.length - nodeCount;
      if (elements.length === 0) {
        this.graph.labelAlways = false;
        this._setGraphStatus(
          "Edge filter \u2014 no relationships selected. Click a relationship chip (or \u201Call\u201D) to show its subgraph."
        );
        return;
      }
      const small = nodeCount <= _LABEL_ALWAYS_NODES;
      this.graph.labelAlways = small;
      if (small) this._applyLabelBucket(2);
      const selected = getEl("graph-layout").value;
      const cloudLayout = selected === "fcose" && !this.graph.layoutTouched ? cache.positions ? "cached" : "components" : selected;
      this._runGraphLayout(cloudLayout, true);
      this._fitCapped(30, _CLOUD_FIT_MAX_ZOOM);
      if (!small) this._applyLabelBucket(this._labelBucketFor(renderer.zoomValue()));
      if (edgeFilterActive) {
        const shown = cache.data.relationship_types.length - hidden.size;
        this._setGraphStatus(
          `Edge filter \u2014 ${nodeCount} entities \xB7 ${edgeCount} edges (${shown} of ${cache.data.relationship_types.length} relationship types)`
        );
      } else {
        const filterNote = everything ? "everything" : "min degree \u2265 2";
        this._setGraphStatus(
          `Full graph \u2014 ${nodeCount} entities \xB7 ${edgeCount} edges (${filterNote})`
        );
      }
    }
    /**
     * Fit the canvas to its elements, then clamp the zoom (see
     * GraphRenderer.fitCapped — sigma's normalized camera keeps node sizes
     * screen-constant, so the small-graph balloon the cap fixed in
     * cytoscape does not arise; the cap remains a sanity bound).
     */
    _fitCapped(padding, maxZoom, focusId) {
      this.graph?.renderer?.fitCapped(padding, maxZoom, focusId);
    }
    /**
     * All-mode search: centre the camera on `name`'s connected set within the
     * CURRENT filter and highlight it. Returns false when the entity is not
     * on the canvas (caller falls back to the Ego jump).
     */
    _spotlightInCloud(name) {
      const renderer = this.graph && this.graph.renderer;
      if (!renderer || !this.graph) return false;
      if (!renderer.hasNode(name)) return false;
      this._highlightCloudSet(renderer.nodeAttrs(name));
      const count = renderer.spotlight(name);
      this._setGraphStatus(`Spotlight \u2014 ${name} \xB7 ${count} entities in its connected set`);
      return true;
    }
    /**
     * Highlight the connected set (component) a tapped node belongs to:
     * reducer-level in-set emphasis for its members, everything else fades
     * toward the background.
     */
    _highlightCloudSet(nodeData) {
      if (!this.graph || this.graph.mode !== "all") return;
      this.graph.renderer?.highlightComponent(nodeData?.component ?? null);
    }
    /** Remove the cloud set-highlight (restores full opacity). */
    _clearCloudHighlight() {
      this.graph?.renderer?.highlightComponent(null);
    }
    /**
     * Legend with INTERACTIVE edge chips: clicking a chip hides/shows that
     * relationship type client-side (no refetch). "All/none" restore.
     */
    _renderCloudLegend(data) {
      const legend = getEl("graph-cloud-legend");
      const nodeTypes = [...new Set(data.nodes.map((n) => n.entity_type))].sort();
      const nodeHtml = nodeTypes.map(
        (t) => `
            <span class="cloud-legend-chip">
                <span class="cloud-swatch cloud-node-${CSS.escape(t)}"></span>${escapeHtml(t)}
            </span>`
      ).join("");
      const chips = data.relationship_types.map((t) => {
        const off = this.graph?.hiddenEdgeTypes.has(t.edge_type) ? " off" : "";
        return `<button type="button" class="edge-chip${off}"
                            data-edge-type="${escapeHtml(t.edge_type)}"
                            title="${escapeHtml(t.semantics)} \u2014 ${t.count} edge${t.count !== 1 ? "s" : ""}. Click to hide/show.">
                        <span class="dot" style="background:${edgeColor(t.edge_type)}"></span>
                        ${escapeHtml(t.edge_type)}
                        <span class="chip-count">${t.count}</span>
                    </button>`;
      }).join("");
      legend.innerHTML = `
            <div class="cloud-legend-group"><strong>Entities</strong>
                <div class="cloud-legend-chips">${nodeHtml}</div></div>
            <div class="cloud-legend-group"><strong>Relationships \u2014 click to hide \xB7 double-click to isolate</strong>
                <div class="cloud-legend-chips">${chips}
                    <button type="button" class="edge-chip edge-chip-all" data-edge-type="__all">all</button>
                    <button type="button" class="edge-chip edge-chip-all" data-edge-type="__none">none</button>
                </div></div>`;
      legend.querySelectorAll(".edge-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          const t = chip.dataset.edgeType;
          if (!t) return;
          if (t === "__all" || t === "__none") this._setAllEdgeTypes(t === "__all");
          else this._toggleEdgeType(t);
        });
        chip.addEventListener("dblclick", () => {
          const t = chip.dataset.edgeType;
          if (t && !t.startsWith("__")) this._soloEdgeType(t);
        });
      });
    }
    /** Reflect hiddenEdgeTypes on every chip (legend + relationship cloud). */
    _syncChipStates() {
      document.querySelectorAll(
        ".edge-chip[data-edge-type], .rel-cloud-chip[data-edge-type]"
      ).forEach((chip) => {
        const t = chip.dataset.edgeType;
        if (!t || t.startsWith("__")) return;
        chip.classList.toggle("off", this.graph.hiddenEdgeTypes.has(t));
      });
    }
    /** Double-click a chip: show ONLY that relationship type (isolate) —
     *  the one-click answer to "see only acquisitions" that previously
     *  needed the none → chip dance. Restore with the "all" chip. */
    _soloEdgeType(t) {
      const cache = this.graph && this.graph.cloud;
      if (!cache || !t) return;
      cache.data.relationship_types.forEach((rt) => {
        if (rt.edge_type === t) this.graph.hiddenEdgeTypes.delete(rt.edge_type);
        else this.graph.hiddenEdgeTypes.add(rt.edge_type);
      });
      this._syncChipStates();
      this._applyCloudFilter();
    }
    /** Show/hide one relationship type: rebuild the induced subgraph. */
    _toggleEdgeType(t) {
      if (!this.graph || !this.graph.cloud || !t) return;
      const nowHidden = !this.graph.hiddenEdgeTypes.has(t);
      if (nowHidden) this.graph.hiddenEdgeTypes.add(t);
      else this.graph.hiddenEdgeTypes.delete(t);
      this._syncChipStates();
      this._applyCloudFilter();
    }
    /** Show or hide every relationship type at once (all / none chips). */
    _setAllEdgeTypes(show) {
      const cache = this.graph && this.graph.cloud;
      if (!cache) return;
      cache.data.relationship_types.forEach((rt) => {
        if (show) this.graph.hiddenEdgeTypes.delete(rt.edge_type);
        else this.graph.hiddenEdgeTypes.add(rt.edge_type);
      });
      this._syncChipStates();
      this._applyCloudFilter();
    }
    /** Relationship cloud card: one size-proportional chip per edge type. */
    _renderRelationshipCloud(types) {
      const card = getEl("graph-relationship-cloud");
      if (!types.length) {
        card.innerHTML = '<p class="hint">No relationships in the graph.</p>';
        return;
      }
      const max = Math.max(...types.map((t) => t.count), 1);
      const chips = types.map((t) => {
        const ratio = t.count / max;
        const size = 0.85 + ratio * 1.35;
        const off = this.graph?.hiddenEdgeTypes.has(t.edge_type) ? " off" : "";
        return `<button type="button" class="rel-cloud-chip${off}"
                            data-edge-type="${escapeHtml(t.edge_type)}"
                            title="${escapeHtml(`${t.semantics} \u2014 ${t.count} edge${t.count !== 1 ? "s" : ""}`)}"
                            style="font-size:${size.toFixed(2)}rem; color:${edgeColor(t.edge_type)};">
                    ${escapeHtml(t.edge_type)}
                    <span class="rel-cloud-count">${t.count} ${t.symmetric ? "\u2194" : "\u2192"}</span>
                </button>`;
      }).join("");
      card.innerHTML = `<h4 class="rel-cloud-title"><i class="fas fa-cloud"></i> Relationship Cloud</h4>
                          <div class="rel-cloud-chips">${chips}</div>`;
      card.querySelectorAll(".rel-cloud-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          const et = chip.dataset.edgeType;
          if (et) this._toggleEdgeType(et);
        });
        chip.addEventListener("dblclick", () => {
          const et = chip.dataset.edgeType;
          if (et) this._soloEdgeType(et);
        });
      });
    }
    /** Louvain community shading (All mode): fetch once, colour nodes. */
    async _applyCommunityShading(on) {
      const renderer = this.graph && this.graph.renderer;
      const cache = this.graph && this.graph.cloud;
      if (!renderer || !cache) return;
      if (!on) {
        renderer.setCommunities(null);
        return;
      }
      if (!cache.communities) {
        try {
          const m = await fetchJson(
            "/api/graph/metrics/louvain_community"
          );
          const map = /* @__PURE__ */ new Map();
          m.groups.forEach((g) => g.members.forEach((name) => map.set(name, g.label)));
          cache.communities = map;
        } catch (e) {
          this._setGraphStatus(
            `communities unavailable: ${e.message} (run make recompute-graph)`
          );
          getEl("cloud-community").checked = false;
          return;
        }
      }
      renderer.setCommunities(cache.communities);
      this._setGraphStatus(
        `Louvain shading on \u2014 ${cache.communities.size} entities in communities`
      );
    }
    // --- Rank mode (S4): metric league tables + side intelligence -------- //
    /** Load every Rank panel (table, louvain groups, suggestions). */
    async _loadRankView() {
      if (!this.graph) return;
      void this._loadRankTable();
      void this._loadRankGroups();
      void this._loadSuggestions();
    }
    /** League table for the selected metric (scalar / link-prediction / seeds). */
    async _loadRankTable() {
      if (!this.graph) return;
      const wrap = getEl("rank-table");
      const metric = getEl("rank-metric").value;
      const top = parseInt(getEl("rank-top").value, 10) || 25;
      const blurb = METRIC_BLURBS[metric] || metric;
      const loading = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> computing ${escapeHtml(metric)}\u2026</p>`;
      const fail = (e) => `<p class="hint">unavailable \u2014 ${escapeHtml(e.message)} (is <span class="mono">make recompute-graph</span> fresh?)</p>`;
      if (metric === "voterank") {
        let seeds = this.graph.rankSeeds;
        if (!seeds) {
          wrap.innerHTML = loading;
          try {
            const data2 = await fetchJson(
              "/api/graph/metrics/voterank"
            );
            seeds = data2.seeds;
            this.graph.rankSeeds = seeds;
          } catch (e) {
            wrap.innerHTML = fail(e);
            return;
          }
        }
        const rows2 = seeds.slice(0, top).map(
          (name, i) => `
                <tr>
                    <td class="idx num">${i + 1}</td>
                    <td><button type="button" class="rank-entity" data-centre="${escapeHtml(name)}"
                                title="Centre the Lens on ${escapeHtml(name)}">${escapeHtml(name)}</button></td>
                    <td class="num muted">seed ${i + 1} of ${seeds.length}</td>
                </tr>`
        ).join("");
        wrap.innerHTML = `<p class="panel-note mono">${escapeHtml(blurb)}</p>
                <table class="rank-table">
                    <thead><tr><th class="num">#</th><th>entity</th><th class="num">note</th></tr></thead>
                    <tbody>${rows2}</tbody>
                </table>
                <p class="panel-note mono">${Math.min(seeds.length, top)} of ${seeds.length} seeds</p>`;
        this._wireCentre(wrap);
        this._setGraphStatus(
          `Rank \u2014 voterank \xB7 ${Math.min(seeds.length, top)} of ${seeds.length} seeds`
        );
        return;
      }
      const key = `${metric}:${top}`;
      if (metric === "link_prediction") {
        let data2 = this.graph.rankData.get(key);
        if (!data2) {
          wrap.innerHTML = loading;
          try {
            data2 = await fetchJson(
              `/api/graph/metrics/${metric}?top=${top}`
            );
            this.graph.rankData.set(key, data2);
          } catch (e) {
            wrap.innerHTML = fail(e);
            return;
          }
        }
        const rows2 = data2.entities.slice(0, top).map((ent, i) => {
          const best = ent.candidates[0];
          return `
                <tr>
                    <td class="idx num">${i + 1}</td>
                    <td><button type="button" class="rank-entity" data-centre="${escapeHtml(ent.entity)}"
                                title="Centre the Lens on ${escapeHtml(ent.entity)}">${escapeHtml(ent.entity)}</button></td>
                    <td>${best ? `<button type="button" class="rank-entity muted-entity" data-centre="${escapeHtml(best.name)}"
                                title="Centre the Lens on ${escapeHtml(best.name)}">\u2194 ${escapeHtml(best.name)}</button>` : '<span class="muted">\u2014</span>'}</td>
                    <td class="num">${this._fmtScore(ent.best_score)}</td>
                </tr>`;
        }).join("");
        wrap.innerHTML = `<p class="panel-note mono">${escapeHtml(blurb)}</p>
                <table class="rank-table">
                    <thead><tr><th class="num">#</th><th>entity</th><th>predicted partner</th><th class="num">best</th></tr></thead>
                    <tbody>${rows2}</tbody>
                </table>
                <p class="panel-note mono">${Math.min(data2.entities.length, top)} of ${data2.total} scored entities</p>`;
        this._wireCentre(wrap);
        this._setGraphStatus(
          `Rank \u2014 link prediction \xB7 ${Math.min(data2.entities.length, top)} of ${data2.total}`
        );
        return;
      }
      let data = this.graph.rankData.get(key);
      if (!data) {
        wrap.innerHTML = loading;
        try {
          data = await fetchJson(
            `/api/graph/metrics/${metric}?top=${top}`
          );
          this.graph.rankData.set(key, data);
        } catch (e) {
          wrap.innerHTML = fail(e);
          return;
        }
      }
      if (!data.ranked.length) {
        wrap.innerHTML = `<p class="hint">No rows for ${escapeHtml(metric)} \u2014 run <span class="mono">make recompute-graph</span>.</p>`;
        return;
      }
      const max = Math.max(...data.ranked.map((r) => r.value), 0);
      const rows = data.ranked.map(
        (r, i) => `
            <tr>
                <td class="idx num">${i + 1}</td>
                <td><button type="button" class="rank-entity" data-centre="${escapeHtml(r.entity)}"
                            title="Centre the Lens on ${escapeHtml(r.entity)}">${escapeHtml(r.entity)}</button></td>
                <td class="num"><span class="score-bar" style="width:${max > 0 ? Math.max(2, Math.round(r.value / max * 46)) : 0}px"></span>
                    ${this._fmtScore(r.value)}</td>
            </tr>`
      ).join("");
      wrap.innerHTML = `<p class="panel-note mono">${escapeHtml(blurb)}</p>
            <table class="rank-table">
                <thead><tr><th class="num">#</th><th>entity</th><th class="num">score</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
            <p class="panel-note mono">${data.ranked.length} of ${data.total} entities \xB7 ${escapeHtml(metric)}</p>`;
      this._wireCentre(wrap);
      this._setGraphStatus(`Rank \u2014 ${metric} \xB7 ${data.ranked.length} of ${data.total}`);
    }
    /** Louvain groups side panel (top groups by size, clickable members). */
    async _loadRankGroups() {
      if (!this.graph) return;
      const mount = getEl("rank-groups");
      if (!this.graph.rankGroups) {
        mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> loading\u2026</p>`;
        try {
          this.graph.rankGroups = await fetchJson(
            "/api/graph/metrics/louvain_community"
          );
        } catch (e) {
          mount.innerHTML = `<p class="hint">unavailable \u2014 ${escapeHtml(e.message)}</p>`;
          return;
        }
      }
      const groups = [...this.graph.rankGroups.groups].sort((a, b) => b.size - a.size).slice(0, 8);
      if (!groups.length) {
        mount.innerHTML = `<p class="hint">No communities \u2014 run <span class="mono">make recompute-graph</span>.</p>`;
        return;
      }
      mount.innerHTML = groups.map((g) => {
        const color = COMMUNITY_PALETTE[g.label % COMMUNITY_PALETTE.length];
        const members = g.members.slice(0, 6).map(
          (m) => `<button type="button" class="chip-entity" data-centre="${escapeHtml(m)}"
                         title="Centre the Lens on ${escapeHtml(m)}">${escapeHtml(m)}</button>`
        ).join("");
        const more = g.members.length > 6 ? `<span class="chip-more">+${g.members.length - 6}</span>` : "";
        return `
            <div class="rank-group">
                <div class="rank-group-head">
                    <span class="swatch" style="background:${color}"></span>
                    <span class="mono">group ${g.label}</span>
                    <span class="cnt mono">${g.size}</span>
                </div>
                <div class="rank-group-members">${members}${more}</div>
            </div>`;
      }).join("") + (this.graph.rankGroups.modularity !== void 0 ? `<p class="panel-note mono">modularity ${this.graph.rankGroups.modularity.toFixed(3)}</p>` : "");
      this._wireCentre(mount);
    }
    /** Read-only link-prediction suggestions (side panel, per method). */
    async _loadSuggestions() {
      if (!this.graph) return;
      const mount = getEl("rank-suggestions");
      const method = getEl("suggest-method").value;
      let rows = this.graph.suggestions.get(method);
      if (!rows) {
        mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> predicting\u2026</p>`;
        const minScore = method === "pref-attach" ? 0 : 0.3;
        try {
          const data = await fetchJson(
            `/api/graph/suggestions?method=${method}&top=15&min_score=${minScore}`
          );
          rows = data.suggestions;
          this.graph.suggestions.set(method, rows);
        } catch (e) {
          mount.innerHTML = `<p class="hint">unavailable \u2014 ${escapeHtml(e.message)}</p>`;
          return;
        }
      }
      if (!rows.length) {
        mount.innerHTML = `<p class="hint">No pairs above the threshold.</p>`;
        return;
      }
      const max = Math.max(...rows.map((r) => r.score), 1e-4);
      mount.innerHTML = rows.map(
        (r, i) => `
            <button type="button" class="suggest-row" data-centre="${escapeHtml(r.source)}"
                    title="${escapeHtml(r.source)} \u2194 ${escapeHtml(r.target)}${r.edition ? ` \xB7 ${escapeHtml(r.edition)}` : ""} \u2014 centre on ${escapeHtml(r.source)}">
                <span class="idx mono">${i + 1}</span>
                <span class="suggest-pair">${escapeHtml(r.source)} <span class="arrow">\u2194</span> ${escapeHtml(r.target)}</span>
                <span class="suggest-bar"><span style="width:${(r.score / max * 100).toFixed(1)}%"></span></span>
                <span class="val mono">${this._fmtScore(r.score)}</span>
            </button>`
      ).join("");
      this._wireCentre(mount);
    }
    // --- Time mode (S4): temporal formation -------------------------------- //
    /** Load every Time panel except near-duplicates (explicit, on-demand). */
    async _loadTimeView() {
      if (!this.graph) return;
      this._setGraphStatus("Time \u2014 loading...");
      await Promise.allSettled([this._loadByYear(), this._loadBridges(), this._loadCoMentions()]);
    }
    /** Deal-activity-by-year stacked bars (M&A + JV edges per year). */
    async _loadByYear() {
      if (!this.graph) return;
      const mount = getEl("time-byyear");
      if (!this.graph.timeByYear) {
        mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> loading\u2026</p>`;
        try {
          this.graph.timeByYear = await fetchJson(
            "/api/graph/edges-by-year"
          );
        } catch (e) {
          mount.innerHTML = `<p class="hint">unavailable \u2014 ${escapeHtml(e.message)}</p>`;
          return;
        }
      }
      const rows = this.graph.timeByYear.timeline;
      if (!rows.length) {
        mount.innerHTML = `<p class="hint">No dated M&A / JV edges in the graph.</p>`;
        getEl("time-legend").innerHTML = "";
        return;
      }
      const types = [...new Set(rows.map((r) => r.edge_type))].sort();
      getEl("time-legend").innerHTML = types.map(
        (t) => `<span class="legend-key"><span class="dot" style="background:${edgeColor(t)}"></span>${escapeHtml(t)}</span>`
      ).join("");
      const byYear = /* @__PURE__ */ new Map();
      rows.forEach((r) => {
        const y = byYear.get(r.year) || { total: 0, parts: [] };
        y.total += r.count;
        y.parts.push(r);
        byYear.set(r.year, y);
      });
      const years = [...byYear.keys()].sort();
      const max = Math.max(...years.map((y) => byYear.get(y).total), 1);
      mount.innerHTML = years.map((y) => {
        const { total, parts } = byYear.get(y);
        const segs = parts.map(
          (p) => `<span class="bar-seg" style="width:${(p.count / total * 100).toFixed(2)}%;background:${edgeColor(p.edge_type)}"
                       title="${escapeHtml(p.edge_type)}: ${p.count}"></span>`
        ).join("");
        return `
            <div class="year-row">
                <span class="yr mono">${escapeHtml(y)}</span>
                <span class="bar-track" style="width:${(total / max * 100).toFixed(1)}%">${segs}</span>
                <span class="cnt mono">${total}</span>
            </div>`;
      }).join("");
      const grand = rows.reduce((a, r) => a + r.count, 0);
      this._setGraphStatus(`Time \u2014 ${grand} dated deals across ${years.length} years`);
    }
    /** Cross-sector bridges table (M&A + JV between sector pairs). */
    async _loadBridges() {
      if (!this.graph) return;
      const mount = getEl("time-bridges");
      if (!this.graph.timeBridges) {
        mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> loading\u2026</p>`;
        try {
          this.graph.timeBridges = await fetchJson("/api/graph/bridges");
        } catch (e) {
          mount.innerHTML = `<p class="hint">unavailable \u2014 ${escapeHtml(e.message)}</p>`;
          return;
        }
      }
      const rows = [...this.graph.timeBridges.bridges].sort((a, b) => b.count - a.count).slice(0, 12);
      if (!rows.length) {
        mount.innerHTML = `<p class="hint">No cross-sector M&A / JV edges yet.</p>`;
        return;
      }
      mount.innerHTML = rows.map(
        (b) => `
            <div class="bridge-row">
                <span class="edge-dot" style="background:${edgeColor(b.edge_type)}"
                      title="${escapeHtml(b.edge_type)}"></span>
                <span class="bridge-pair">${escapeHtml(b.sector_a)} <span class="arrow">\u2194</span> ${escapeHtml(b.sector_b)}</span>
                <span class="cnt mono">${b.count}</span>
            </div>`
      ).join("");
    }
    /** Co-mention leaderboard (most-connected entities in prose). */
    async _loadCoMentions() {
      if (!this.graph) return;
      const mount = getEl("time-comentions");
      if (!this.graph.timeCoMentions) {
        mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> loading\u2026</p>`;
        try {
          this.graph.timeCoMentions = await fetchJson(
            "/api/graph/co-mentions?top=15"
          );
        } catch (e) {
          mount.innerHTML = `<p class="hint">unavailable \u2014 ${escapeHtml(e.message)}</p>`;
          return;
        }
      }
      const rows = this.graph.timeCoMentions.ranked;
      if (!rows.length) {
        mount.innerHTML = `<p class="hint">No co-mentions derived yet.</p>`;
        return;
      }
      const max = Math.max(...rows.map((r) => r.co_mentions), 1);
      mount.innerHTML = rows.map(
        (r) => `
            <div class="cm-row">
                <button type="button" class="rank-entity" data-centre="${escapeHtml(r.entity)}"
                        title="Centre the Lens on ${escapeHtml(r.entity)}">${escapeHtml(r.entity)}</button>
                <span class="cm-bar"><span style="width:${(r.co_mentions / max * 100).toFixed(1)}%"></span></span>
                <span class="cnt mono">${r.co_mentions}</span>
            </div>`
      ).join("");
      this._wireCentre(mount);
    }
    /** Near-duplicate triage — explicit run only (the ~1s pairwise scan). */
    async _loadNearDuplicates() {
      if (!this.graph) return;
      const btn = getEl("neardup-run");
      const mount = getEl("time-neardup");
      if (this.graph.nearDup) {
        this._renderNearDuplicates(this.graph.nearDup);
        return;
      }
      btn.disabled = true;
      mount.innerHTML = `<p class="hint"><i class="fas fa-spinner fa-spin"></i> comparing note embeddings (~1s)\u2026</p>`;
      try {
        this.graph.nearDup = await fetchJson(
          "/api/graph/near-duplicates?min_sim=0.9&limit=50"
        );
      } catch (e) {
        mount.innerHTML = `<p class="hint">unavailable \u2014 ${escapeHtml(e.message)}</p>`;
        return;
      } finally {
        btn.disabled = false;
      }
      this._renderNearDuplicates(this.graph.nearDup);
    }
    _renderNearDuplicates(data) {
      const mount = getEl("time-neardup");
      if (!data.pairs.length) {
        mount.innerHTML = `<p class="hint">Clean \u2014 no pairs at cosine \u2265 ${data.min_sim.toFixed(2)}.</p>`;
        return;
      }
      mount.innerHTML = data.pairs.map(
        (p) => `
            <div class="neardup-row">
                <span class="sim mono">${p.similarity.toFixed(3)}</span>
                <a href="/entity/${encodeURI(p.path_a)}" target="_blank" rel="noopener">${escapeHtml(p.title_a || p.path_a)}</a>
                <span class="arrow">\u2194</span>
                <a href="/entity/${encodeURI(p.path_b)}" target="_blank" rel="noopener">${escapeHtml(p.title_b || p.path_b)}</a>
            </div>`
      ).join("");
    }
    // --- Shared S4 helpers --------------------------------------------------- //
    /** Jump the Lens to an ego view of `name` (Rank/Time click-outs). */
    _centreOn(name) {
      getEl("graph-search").value = name;
      this._setMode("ego");
      void this.loadEgoNetwork(name);
    }
    /** Wire every [data-centre] button inside `root` to the ego jump. */
    _wireCentre(root) {
      root.querySelectorAll("[data-centre]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const n = btn.dataset.centre;
          if (n) this._centreOn(n);
        });
      });
    }
    /** Compact score formatting for the mono data voice. */
    _fmtScore(v) {
      const a = Math.abs(v);
      if (a === 0) return "0";
      if (a >= 100) return v.toFixed(0);
      if (a >= 1) return v.toFixed(2);
      if (a >= 1e-3) return v.toFixed(4);
      return v.toExponential(1);
    }
    // --- Ego mode ---------------------------------------------------------- //
    async loadEgoNetwork(name) {
      this._setGraphStatus(`Loading ${name}...`);
      const asOf = this._asOf();
      const params = new URLSearchParams();
      if (asOf) params.set("as_of", asOf);
      const qs = params.toString();
      let data;
      try {
        const url = `/api/graph/neighbors/${encodeURIComponent(name)}` + (qs ? `?${qs}` : "");
        data = await fetchJson(url);
      } catch (e) {
        this._setGraphStatus(`Error: ${e.message}`);
        return;
      }
      if (!this.graph || !this.graph.renderer) return;
      const isSector = data.entity_type === "sector";
      const filter = isSector ? "all" : getEl("graph-filter").value;
      const elements = isSector ? this._buildSectorEgoElements(data) : this._bundleElements(data, filter, "focal");
      this.graph.renderer.setElements(elements);
      this.graph.central = isSector ? data.sector : data.company;
      this.graph.elements = elements;
      this.graph.entityType = isSector ? "sector" : "company";
      this._runGraphLayout(
        getEl("graph-layout").value,
        false,
        true,
        this.graph.central
      );
      this._fitCapped(40, _EGO_FIT_MAX_ZOOM, this.graph.central || void 0);
      this.graph.labelAlways = false;
      this._applyLabelBucket(-1);
      this.graph.renderer.setFocal(this.graph.central);
      this.graph.focalBundle = data;
      this._renderGraphDetail(this.graph.renderer.nodeAttrs(this.graph.central), data);
      const asOfSuffix = asOf ? ` \xB7 as of ${asOf}` : "";
      if (isSector) {
        const sectorBundle = data;
        const capped = sectorBundle.member_count > _SECTOR_RENDER_CAP;
        this._setGraphStatus(
          `${sectorBundle.sector} \u2014 ${sectorBundle.member_count} member` + (sectorBundle.member_count !== 1 ? "s" : "") + (capped ? ` (rendering first ${_SECTOR_RENDER_CAP})` : "") + asOfSuffix
        );
      } else {
        const companyBundle = data;
        const counts = {
          peers: companyBundle.peers.length,
          jv: companyBundle.jv_partners.length,
          siblings: companyBundle.group_siblings.length,
          acquired: companyBundle.acquired.length,
          suppliers: companyBundle.suppliers.length,
          customers: companyBundle.customers.length
        };
        const total = Object.values(counts).reduce((a, b) => a + b, 0);
        this._setGraphStatus(
          `${companyBundle.company} \u2014 ${total} relationship${total !== 1 ? "s" : ""}` + (companyBundle.sector ? ` \xB7 ${companyBundle.sector}` : "") + asOfSuffix
        );
      }
      getEl("graph-empty").style.display = "none";
    }
    /**
     * Sector ego elements: the focal sector + one edge per member company.
     * S3: every member renders (guard cap only) — the old 60-member synthetic
     * "+N more" node is gone.
     */
    _buildSectorEgoElements(data) {
      const focal = data.sector;
      const all = (data.members || []).slice(0, _SECTOR_RENDER_CAP);
      const nodes = [
        { data: { id: focal, label: focal, group: "sector-focal", centrality: 10 } }
      ];
      const edges = [];
      all.forEach((m) => {
        nodes.push({ data: { id: m, label: m, group: "member", centrality: 5 } });
        edges.push({
          data: {
            id: `${focal}__${m}`,
            source: focal,
            target: m,
            type: "has_company",
            label: "has"
          }
        });
      });
      return [...nodes, ...edges];
    }
    /**
     * Company ego elements from the neighbors bundle. `focalGroup` is "focal"
     * for the canvas centre or "outer" when the same builder merges a second
     * ring in during progressive expansion (the focal node already exists).
     */
    _bundleElements(data, filter, focalGroup) {
      const nodes = [];
      const edges = [];
      const focal = data.company;
      const addNode = (name, group) => {
        if (!name || name === focal) return;
        nodes.push({ data: { id: name, label: name, group, centrality: 0 } });
      };
      const addEdge = (src, dst, type, label, props = {}) => {
        edges.push({
          data: {
            id: `${src}__${dst}__${type}`,
            source: src,
            target: dst,
            type,
            label,
            props
          }
        });
      };
      if (focalGroup === "focal") {
        nodes.push({ data: { id: focal, label: focal, group: "focal", centrality: 10 } });
      }
      if (filter === "all" || filter === "peers") {
        data.peers.forEach((p) => {
          addNode(p, "peer");
          addEdge(focal, p, "competes_with", "peer");
        });
      }
      if (filter === "all" || filter === "jv") {
        data.jv_partners.forEach((j) => {
          addNode(j.partner, "jv");
          addEdge(focal, j.partner, "jv_with", "JV" + (j.venture ? `: ${j.venture}` : ""));
        });
      }
      if (filter === "all") {
        data.group_siblings.forEach((s) => {
          addNode(s, "sibling");
          addEdge(focal, s, "same_group", "same group");
        });
      }
      if (filter === "all" || filter === "acquired") {
        data.acquired.forEach((a) => {
          addNode(a.name, "acquired");
          addEdge(focal, a.name, "acquired", "acquired" + (a.year ? ` ${a.year}` : ""));
        });
      }
      if (filter === "all" || filter === "subsidiary") {
        if (data.subsidiary_of) {
          addNode(data.subsidiary_of, "parent");
          addEdge(data.subsidiary_of, focal, "subsidiary_of", "parent of");
        }
      }
      if (filter === "all" || filter === "supply") {
        data.suppliers.forEach((s) => {
          addNode(s, "supplier");
          addEdge(s, focal, "supplier_to", "supplies");
        });
        data.customers.forEach((c) => {
          addNode(c, "customer");
          addEdge(focal, c, "customer_of", "customer");
        });
      }
      if (filter === "all" && data.sector) {
        const sectorId = `sector:${data.sector}`;
        nodes.push({
          data: { id: sectorId, label: data.sector, group: "sector", centrality: 8 }
        });
        edges.push({
          data: {
            id: `${focal}__${sectorId}`,
            source: focal,
            target: sectorId,
            type: "part_of",
            label: "part of"
          }
        });
      }
      nodes.forEach((n) => {
        if (n.data.group === "peer") n.data.centrality = 6;
        else if (n.data.group === "parent") n.data.centrality = 7;
        else if (n.data.group === "sector") n.data.centrality = 8;
      });
      return [...nodes, ...edges];
    }
    /**
     * Progressive expansion: fetch the bundle for `name` and merge its
     * not-yet-present nodes/edges into the canvas as an "outer" ring,
     * keeping existing positions (the renderer seeds new nodes around the
     * anchor and relaxes with a short FA2 pass — the old fcose-without-
     * randomize role).
     */
    async _expandNode(name) {
      const renderer = this.graph && this.graph.renderer;
      if (!renderer || !this.graph) return;
      if (renderer.nodeCount() >= _EXPAND_NODE_CAP) {
        this._setGraphStatus(
          `expansion cap (${_EXPAND_NODE_CAP} nodes) \u2014 centre on ${name} to continue there`
        );
        return;
      }
      this._setGraphStatus(`Adding neighbours of ${name}...`);
      let data;
      try {
        data = await fetchJson(
          `/api/graph/neighbors/${encodeURIComponent(name)}`
        );
      } catch (e) {
        this._setGraphStatus(`Error: ${e.message}`);
        return;
      }
      const els = data.entity_type === "sector" ? this._buildSectorEgoElements(data) : this._bundleElements(data, "all", "outer");
      els.forEach((el) => {
        if (!el.data.source && !renderer.hasNode(el.data.id)) el.data.group = "outer";
      });
      const added = renderer.mergeElements(els, name);
      this._fitCapped(40, _EGO_FIT_MAX_ZOOM);
      this._setGraphStatus(`+${added} nodes from ${name} \xB7 ${renderer.nodeCount()} on canvas`);
    }
    // --- Zoom, tooltips, zoom-fade labels ---------------------------------- //
    /** Wire the zoom slider / buttons / fit (buckets sync via camera events). */
    _initGraphZoom() {
      if (!this.graph || !this.graph.renderer) return;
      const renderer = this.graph.renderer;
      const slider = getEl("graph-zoom");
      const label = getEl("graph-zoom-label");
      const applyZoom = () => {
        const z = parseFloat(slider.value) || 1;
        renderer.setZoomValue(z);
        label.textContent = `${Math.round(z * 100)}%`;
      };
      slider.addEventListener("input", applyZoom);
      getEl("graph-zoom-in").addEventListener("click", () => renderer.zoomIn());
      getEl("graph-zoom-out").addEventListener("click", () => renderer.zoomOut());
      getEl("graph-zoom-fit").addEventListener(
        "click",
        () => renderer.fitCapped(30, _CLOUD_FIT_MAX_ZOOM)
      );
      this._syncZoomUi();
    }
    /** Slider + % label ← renderer camera (wheel / pinch / buttons / animation). */
    _syncZoomUi() {
      const renderer = this.graph?.renderer;
      if (!renderer) return;
      const slider = getEl("graph-zoom");
      const label = getEl("graph-zoom-label");
      const z = renderer.zoomValue();
      slider.value = String(Math.min(3, Math.max(0.2, z)));
      label.textContent = `${Math.round(z * 100)}%`;
    }
    /** Camera moved: tooltip hides, slider re-syncs, buckets recompute. */
    _onCameraChange() {
      this._hideTip();
      this._syncZoomUi();
      if (this.graph && this.graph.mode === "all" && !this.graph.labelAlways) {
        this._applyLabelBucket(this._labelBucketFor(this.graph.renderer?.zoomValue() || 1));
      }
    }
    /** Bucket for a zoom value in slider units (z = 1/camera-ratio). */
    _labelBucketFor(z) {
      if (z < 1 / _RATIO_LBL_OFF) return 0;
      if (z < 1 / _RATIO_LBL_HUBS) return 1;
      return 2;
    }
    /**
     * Apply a zoom-fade label bucket (cloud nodes only — ego labels are
     * always on; the reducer gates labels by the bucket + hub flag).
     * Bucket -1 clears the gate. Only refreshes on bucket CHANGE.
     */
    _applyLabelBucket(bucket) {
      if (!this.graph?.renderer) return;
      if (bucket === this.graph.labelBucket) return;
      this.graph.labelBucket = bucket;
      this.graph.renderer.setLabelBucket(bucket);
    }
    /** Tooltip placement + content (viewport-positioned, camera-relative). */
    _placeTip(x, y) {
      const tip = getEl("graph-tip");
      const canvas = getEl("graph-canvas");
      const maxX = canvas.clientWidth - 290;
      const maxY = canvas.clientHeight - 90;
      tip.style.left = `${Math.max(4, Math.min(x + 14, maxX))}px`;
      tip.style.top = `${Math.max(4, Math.min(y + 14, maxY))}px`;
      tip.style.display = "block";
    }
    _showNodeTip(d, x, y) {
      const rows = [];
      if (d.cloud) {
        rows.push(
          `<div class="tip-meta">degree ${String(d.deg ?? "?")} \xB7 ${escapeHtml(String(d.group || "entity"))}</div>`
        );
      }
      const community = this.graph?.cloud?.communities?.get(d.id);
      if (community !== void 0) {
        rows.push(`<div class="tip-meta">community ${community}</div>`);
      }
      getEl("graph-tip").innerHTML = `<div class="tip-type">${escapeHtml(String(d.group || "node"))}</div><div class="tip-name">${escapeHtml(String(d.label || ""))}</div>` + rows.join("");
      this._placeTip(x, y);
    }
    _showEdgeTip(d, x, y) {
      const props = d.props || {};
      const extra = Object.keys(props).map((k) => `${k}: ${String(props[k])}`).join(" \xB7 ");
      getEl("graph-tip").innerHTML = `<div class="tip-type">${escapeHtml(String(d.rel || "edge"))}</div><div class="tip-name">${escapeHtml(d.source)} \u2192 ${escapeHtml(d.target)}</div>` + (extra ? `<div class="tip-meta">${escapeHtml(extra)}</div>` : "");
      this._placeTip(x, y);
    }
    _hideTip() {
      const tip = document.getElementById("graph-tip");
      if (tip) tip.style.display = "none";
    }
    // --- Layouts ------------------------------------------------------------ //
    /** Run a layout from the #graph-layout dropdown (renderer owns the
     *  engines: FA2 for the force options, preset assignments otherwise;
     *  "cached" falls back to "components" when the sidecar is cold). */
    _runGraphLayout(name, cloud = false, randomize = true, root = null) {
      if (!this.graph?.renderer) return;
      this.graph.renderer.runLayout(name, {
        cloud,
        randomize,
        cachedPositions: this.graph.cloud?.positions ?? null,
        root
      });
      this._syncZoomUi();
    }
    // --- Inspector (detail panel) ------------------------------------------- //
    _renderGraphDetail(nodeData, bundle) {
      const panel = getEl("graph-detail");
      if (!nodeData) {
        panel.innerHTML = '<div class="graph-detail-empty"><i class="fas fa-hand-pointer"></i><p>Click a node to centre the graph on it.</p></div>';
        return;
      }
      const name = nodeData.id;
      const group = nodeData.group || "company";
      const nb = bundle !== void 0 ? bundle : this.graph?.focalBundle ?? null;
      const isFocal = nb !== null && (nb.entity_type === "sector" ? nb.sector === name : nb.company === name);
      let html = `<div class="graph-detail-header">
            <span class="graph-badge graph-badge-${CSS.escape(group)}">${escapeHtml(group)}</span>
            <h3>${escapeHtml(name)}</h3>
        </div>`;
      if (nb && isFocal && nb.entity_type === "sector") {
        const sectorBundle = nb;
        html += `<ul class="graph-detail-list">`;
        html += `<li><strong>Members:</strong> ${sectorBundle.member_count}</li>`;
        const mc = sectorBundle.market_cap_counts || {};
        Object.keys(mc).forEach((k) => {
          html += `<li><strong>${escapeHtml(k)}:</strong> ${mc[k]}</li>`;
        });
        html += `</ul>`;
        if (sectorBundle.file_path) {
          html += `<a class="btn-primary" href="/entity/${sectorBundle.file_path}">View sector note \u2192</a>`;
        }
      } else if (nb && isFocal) {
        const companyBundle = nb;
        html += `<ul class="graph-detail-list">`;
        if (companyBundle.sector)
          html += `<li><strong>Sector:</strong> ${escapeHtml(companyBundle.sector)}</li>`;
        if (companyBundle.subsidiary_of)
          html += `<li><strong>Parent:</strong> ${escapeHtml(companyBundle.subsidiary_of)}</li>`;
        html += `<li><strong>Peers:</strong> ${companyBundle.peers.length || "\u2014"}</li>`;
        html += `<li><strong>JV partners:</strong> ${companyBundle.jv_partners.length || "\u2014"}</li>`;
        html += `<li><strong>Group siblings:</strong> ${companyBundle.group_siblings.length || "\u2014"}</li>`;
        html += `<li><strong>Acquired:</strong> ${companyBundle.acquired.length || "\u2014"}</li>`;
        html += `<li><strong>Suppliers:</strong> ${companyBundle.suppliers.length || "\u2014"}</li>`;
        html += `<li><strong>Customers:</strong> ${companyBundle.customers.length || "\u2014"}</li>`;
        html += `</ul>`;
        if (companyBundle.file_path) {
          html += `<a class="btn-primary" href="/entity/${companyBundle.file_path}">View full note \u2192</a>`;
        }
      } else {
        html += `<p class="hint">Click this node (or it's already selected) to re-centre on <em>${escapeHtml(name)}</em>.</p>`;
      }
      panel.innerHTML = html;
      if (!isFocal) {
        const row = document.createElement("div");
        row.className = "graph-detail-actions";
        const centreBtn = document.createElement("button");
        centreBtn.className = "btn-primary";
        centreBtn.textContent = `Centre on ${name}`;
        centreBtn.addEventListener("click", () => {
          getEl("graph-search").value = name;
          getEl("graph-search-btn").click();
        });
        const expandBtn = document.createElement("button");
        expandBtn.className = "btn-secondary";
        expandBtn.textContent = `\uFF0B ${name}'s neighbours`;
        expandBtn.title = "Merge this node's neighbours into the canvas (progressive expansion)";
        expandBtn.addEventListener("click", () => this._expandNode(name));
        row.append(centreBtn, expandBtn);
        panel.appendChild(row);
      }
      if (!_NON_EVENT_GROUPS.has(group)) {
        const evMount = document.createElement("div");
        evMount.id = "inspector-events";
        evMount.className = "inspector-events";
        panel.appendChild(evMount);
        void this._loadInspectorEvents(name);
      }
    }
    /**
     * Events timeline for the inspected entity (/api/events — acquisitions,
     * JVs, guidance, management changes, date-ordered). Guarded by a
     * monotonic token so a slow fetch can't paint over a newer selection.
     */
    async _loadInspectorEvents(name) {
      const mount = document.getElementById("inspector-events");
      if (!mount || !this.graph) return;
      const seq = ++this.graph.detailSeq;
      mount.innerHTML = `<h4 class="insp-events-head"><i class="fas fa-timeline"></i> Events</h4>
            <p class="hint"><i class="fas fa-spinner fa-spin"></i></p>`;
      let data;
      try {
        data = await fetchJson(`/api/events/${encodeURIComponent(name)}`);
      } catch {
        if (this.graph.detailSeq === seq) mount.innerHTML = "";
        return;
      }
      if (!this.graph || this.graph.detailSeq !== seq) return;
      if (!data.events.length) {
        mount.innerHTML = `<h4 class="insp-events-head"><i class="fas fa-timeline"></i> Events</h4>
                <p class="hint">None recorded.</p>`;
        return;
      }
      const items = data.events.map((ev) => {
        const bits = [ev.counterparty, ev.magnitude].filter((x) => Boolean(x)).map((x) => escapeHtml(x)).join(" \xB7 ");
        return `
            <li class="tl-item"${ev.source_quote ? ` title="${escapeHtml(ev.source_quote)}"` : ""}>
                <span class="tl-date mono">${this._eventDateLabel(ev)}</span>
                <span class="tl-type">${escapeHtml(ev.event_type)}</span>
                <span class="tl-body">${bits || "&nbsp;"}</span>
            </li>`;
      }).join("");
      mount.innerHTML = `<h4 class="insp-events-head"><i class="fas fa-timeline"></i> Events
                <span class="cnt mono">${data.event_count}</span></h4>
            <ol class="tl">${items}</ol>`;
    }
    /** Date label cut to the stored precision ("2022-07-14" → 2022 / 2022-07 / full). */
    _eventDateLabel(ev) {
      if (!ev.event_date) return "\u2014";
      const d = String(ev.event_date);
      if (ev.date_precision === "year") return d.slice(0, 4);
      if (ev.date_precision === "month") return d.slice(0, 7);
      return d.slice(0, 10);
    }
    _setGraphStatus(text) {
      const el = document.getElementById("graph-status");
      if (el) el.textContent = text;
    }
    // --- Path mode ------------------------------------------------------------ //
    async loadShortestPath() {
      const a = getEl("shortest-a").value.trim();
      const b = getEl("shortest-b").value.trim();
      const result = getEl("shortest-result");
      if (!a || !b) {
        result.innerHTML = '<p class="hint">Enter both entities.</p>';
        return;
      }
      const asOf = this._asOf();
      const params = new URLSearchParams({ a, b });
      if (asOf) params.set("as_of", asOf);
      result.innerHTML = '<p><i class="fas fa-spinner fa-spin"></i> Finding path...</p>';
      try {
        const data = await fetchJson(`/api/graph/shortest?${params}`);
        this._renderShortestPath(data);
      } catch (e) {
        result.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
      }
    }
    _renderShortestPath(data) {
      const result = getEl("shortest-result");
      const renderer = this.graph && this.graph.renderer;
      if (data.path === null) {
        result.innerHTML = `<p class="hint">No path found between <em>${escapeHtml(data.source)}</em> and <em>${escapeHtml(data.target)}</em> within the hop limit` + (this._asOf() ? ` as of ${this._asOf()}` : "") + `.</p>`;
        return;
      }
      const chain = data.path.map((p) => p.name);
      const hops = data.hops ?? 0;
      const ribbon = chain.map(
        (n, i) => `<button type="button" class="hop-chip" data-hop-name="${escapeHtml(n)}" title="Centre the Lens on ${escapeHtml(n)}"><span class="hop-idx">${i + 1}</span>${escapeHtml(n)}</button>`
      ).join(`<span class="hop-arrow">\u2192</span>`);
      result.innerHTML = `
            <div class="path-ribbon">${ribbon}</div>
            <p class="hint">${hops} hop${hops !== 1 ? "s" : ""}` + (this._asOf() ? ` \xB7 as of ${this._asOf()}` : " \xB7 now") + `</p>`;
      result.querySelectorAll(".hop-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          const n = chip.dataset.hopName;
          if (!n) return;
          getEl("graph-search").value = n;
          this._setMode("ego");
          this.loadEgoNetwork(n);
        });
      });
      if (!renderer) return;
      if (this.graph && this.graph.mode === "path") {
        const elements = chain.map((n, i) => ({
          data: {
            id: n,
            label: n,
            group: i === 0 || i === chain.length - 1 ? "path-end" : "company"
          }
        }));
        for (let i = 0; i < chain.length - 1; i++) {
          elements.push({
            data: {
              id: `path__${chain[i]}__${chain[i + 1]}`,
              source: chain[i],
              target: chain[i + 1],
              type: "path-hop",
              label: String(i + 1)
            }
          });
        }
        renderer.setElements(elements);
        this.graph.labelAlways = false;
        this._runGraphLayout("breadthfirst", false, true, chain[0]);
        this._fitCapped(60, _EGO_FIT_MAX_ZOOM);
        this._applyLabelBucket(-1);
        getEl("graph-empty").style.display = "none";
      } else {
        const pathNodes = chain.filter((n) => renderer.hasNode(n));
        if (pathNodes.length === chain.length) {
          renderer.highlightPath(chain);
        } else {
          renderer.highlightPath(null);
        }
      }
    }
    clearShortestPath() {
      getEl("shortest-result").innerHTML = "";
      getEl("shortest-a").value = "";
      getEl("shortest-b").value = "";
      this.graph?.renderer?.highlightPath(null);
    }
  };

  // src/findata.ts
  var FinDataViewer = class {
    constructor() {
      this.companies = new CompaniesView(() => this.router.isActive("companies"));
      this.sectors = new SectorsView(
        () => this.router.isActive("sectors"),
        // Classification-tag click: filter companies + jump over.
        (sector) => {
          this.companies.setSectorFilter(sector);
          this.router.switchView("companies");
        }
      );
      this.stats = new StatsView(() => this.router.isActive("stats"));
      this.docs = new DocsView(() => this.router.isActive("docs"));
      this.graph = new GraphView();
      this.router = new Router({
        companies: () => this.companies.loadEntities(),
        sectors: () => this.sectors.load(),
        stats: () => this.stats.load(),
        graph: () => this.graph.loadGraphView(),
        docs: () => this.docs.loadCatalog()
      });
      this.init();
    }
    init() {
      this.bindEvents();
      this.loadInitialData();
    }
    bindEvents() {
      this.router.bindNav();
      this.companies.bindEvents();
      this.docs.bindEvents();
    }
    async loadInitialData() {
      await this.sectors.load();
      await this.stats.load();
      await this.companies.loadEntities();
    }
    // --- window.viewer inline-onclick surface ----------------------------- //
    // Keep these three exactly as named; generated HTML depends on them.
    /** Pagination buttons (views/companies.ts updatePagination). */
    goToPage(page) {
      this.companies.goToPage(page);
    }
    /** Markdown image lightbox (core/markdown.ts processRichContent). */
    openLightbox(imageSrc) {
      openLightbox(imageSrc);
    }
    /** Code-block copy buttons (core/markdown.ts processRichContent). */
    copyCode(codeId) {
      copyCode(codeId);
    }
  };
  var viewer = new FinDataViewer();
  window.viewer = viewer;
})();
//# sourceMappingURL=findata.bundle.js.map
