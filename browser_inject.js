// 币安Predict 5分钟预测 —— 数据桥接脚本 (支持 BTC/ETH 两个标签页)
// 使用方法:
//   BTC标签页: 打开 https://www.binance.com/es/prediction/market-detail/btc-up-or-down-5m
//   ETH标签页: 打开 https://www.binance.com/es/prediction/market-detail/eth-up-or-down-5m
//   1. 在该标签页按 F12 打开控制台 (Console)
//   2. 粘贴本脚本回车(把下方 SYM 改成对应币种: BTC 或 ETH)，看到 [predict-bridge] started 即成功
//   3. 保持标签页打开(最小化也行)，本机机器人即可收到实时预测数据
(() => {
  const HOST = "http://127.0.0.1:8051";
  const SYM = "BTC"; // 改这里: 在ETH预测页粘贴时改成 "ETH"
  const marketId = new URLSearchParams(location.search).get("marketid") || "";

  async function post(path, obj) {
    try {
      await fetch(HOST + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(obj),
      });
    } catch (e) {
      console.error("[predict-bridge] send fail:", e);
    }
  }

  async function poll() {
    const share = {
      url: location.href,
      title: document.title,
      ts: Date.now(),
      sym: SYM,
    };
    try {
      const m = await fetch(
        "https://www.binance.com/bapi/predictions/v1/friendly/prediction/market?marketId=" + marketId
      );
      if (m.ok) share.market = await m.json();
    } catch (e) { console.error("[predict-bridge] market fail:", e); }

    try {
      const t = await fetch(
        "https://www.binance.com/bapi/predictions/v1/friendly/prediction/market-ticker",
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ marketId: Number(marketId) }) }
      );
      if (t.ok) share.ticker = await t.json();
    } catch (e) { console.error("[predict-bridge] ticker fail:", e); }

    try {
      const h = await fetch(
        "https://www.binance.com/bapi/predictions/v1/friendly/prediction/market-history?marketId=" +
          marketId + "&page=1&rows=5"
      );
      if (h.ok) share.history = await h.json();
    } catch (e) { console.error("[predict-bridge] history fail:", e); }

    try {
      const k = await fetch(
        "https://www.binance.com/api/v3/klines?symbol=" + SYM + "USDT&interval=5m&limit=150"
      );
      if (k.ok) share.klines = await k.json();
    } catch (e) { console.error("[predict-bridge] klines fail:", e); }

    share.dom = document.body ? document.body.innerText.slice(0, 6000) : "";
    post("/api/prediction", share);
  }

  poll();
  setInterval(poll, 3000);
  console.log("[predict-bridge] started -> pushing binance data to", HOST, "SYM =", SYM, "marketId =", marketId || "(页面无marketid参数)");
})();