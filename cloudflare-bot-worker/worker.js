/**
 * ◈ USEGRAVITY TELEGRAM MASTER BOT — CLOUDFLARE WORKER
 * 24/7 Serverless Telegram Bot on Cloudflare Edge
 * Zero dependencies, ultra-fast (<50ms response), 100% Free
 */

import posts from './channel_posts.json';

const BOT_TOKEN = "8743515377:AAF8nFZrgNxptoiMRjuOzMMzFhhhHYTUb9o";
const CHANNEL_ID = "@Wentufolio";
const BASE_URL = "https://deposits-jeremy-comparing-wild.trycloudflare.com";

async function tgRequest(method, data) {
  const url = `https://api.telegram.org/bot${BOT_TOKEN}/${method}`;
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return await resp.json();
}

function getMainKeyboard() {
  return {
    keyboard: [
      [{ text: "🎮 Subway Surfers O'ynash", web_app: { url: `${BASE_URL}/subway-surfers/index.html` } }],
      [{ text: "📢 Navbatdagi Loyiha" }, { text: "📊 Tizim Holati" }],
      [{ text: "💼 Upwork Taklif" }, { text: "🌐 Jonli Portfolio" }],
      [{ text: "🚀 Barcha 66 ta Loyiha" }]
    ],
    resize_keyboard: true,
    persistent: true
  };
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. Webhook Setup
    if (url.pathname === "/init" || url.pathname === "/set-webhook") {
      const webhookUrl = `${url.origin}/webhook`;
      const res = await tgRequest("setWebhook", {
        url: webhookUrl,
        allowed_updates: ["message", "callback_query", "channel_post"]
      });
      return new Response(JSON.stringify({ status: "Cloudflare Worker Webhook Setup", webhookUrl, result: res }, null, 2), {
        headers: { "Content-Type": "application/json" }
      });
    }

    if (request.method !== "POST") {
      return new Response("◈ Usegravity Telegram Bot is LIVE on Cloudflare Workers 24/7!", {
        headers: { "Content-Type": "text/plain; charset=utf-8" }
      });
    }

    // 2. Handle Telegram Webhook Update
    try {
      const update = await request.json();
      const message = update.message;

      if (!message || !message.text) {
        return new Response("OK");
      }

      const chatId = message.chat.id;
      const text = message.text.trim();
      const tLower = text.toLowerCase();
      const userName = message.from?.first_name || "Boss";

      // /start or /help
      if (text.startsWith("/start") || text.startsWith("/help")) {
        const welcome = `Salom, <b>${userName}</b>! ◈ <b>Antigravity AI Boshqaruv Markazi</b> (Cloudflare Workers 24/7).\n\n` +
          `Botingiz endi to'liq <b>Cloudflare Serverless</b> bulutida ishlamoqda. Kompyuteringiz o'chiq bo'lsa ham bot 24/7 javob beradi!\n\n` +
          `📌 <b>Ulangan Kanal:</b> <code>${CHANNEL_ID}</code>\n` +
          `🌐 <b>Jonli Portfolio:</b> ${BASE_URL}\n\n` +
          `Quyidagi tugmalardan birini bosing:`;
        await tgRequest("sendMessage", {
          chat_id: chatId,
          text: welcome,
          parse_mode: "HTML",
          reply_markup: getMainKeyboard()
        });
        return new Response("OK");
      }

      // Navbatdagi Loyiha or /next
      if (text === "📢 Navbatdagi Loyiha" || text.startsWith("/next") || tLower.includes("keyingi")) {
        await tgRequest("sendMessage", { chat_id: chatId, text: "⏳ Navbatdagi loyiha kanalga tayyorlanmoqda..." });
        
        const keys = Object.keys(posts);
        const randKey = keys[Math.floor(Math.random() * keys.length)];
        const p = posts[randKey];

        const postText = p.text.replace("https://ceo-portfolio.pages.dev", BASE_URL);
        const photoRes = await tgRequest("sendPhoto", {
          chat_id: CHANNEL_ID,
          photo: p.img,
          caption: postText
        });

        if (photoRes.ok) {
          await tgRequest("sendMessage", {
            chat_id: chatId,
            text: `✅ Loyiha #${p.num} (${p.title}) muvaffaqiyatli ${CHANNEL_ID} kanaliga joylandi!`,
            reply_markup: getMainKeyboard()
          });
        } else {
          await tgRequest("sendMessage", {
            chat_id: chatId,
            text: `❌ Xatolik: ${photoRes.description || "Yuborib bo'lmadi"}`,
            reply_markup: getMainKeyboard()
          });
        }
        return new Response("OK");
      }

      // /post <num>
      if (text.startsWith("/post")) {
        const numMatch = text.match(/\d{1,2}/);
        if (numMatch) {
          const numStr = numMatch[0].padStart(2, "0");
          const p = posts[numStr];
          if (!p) {
            await tgRequest("sendMessage", { chat_id: chatId, text: `Loyiha #${numStr} topilmadi. (01 dan 66 gacha mavjud)` });
            return new Response("OK");
          }
          const postText = p.text.replace("https://ceo-portfolio.pages.dev", BASE_URL);
          const photoRes = await tgRequest("sendPhoto", {
            chat_id: CHANNEL_ID,
            photo: p.img,
            caption: postText
          });
          const statusText = photoRes.ok
            ? `✅ Loyiha #${numStr} (${p.title}) muvaffaqiyatli kanalga joylandi!`
            : `❌ Xatolik: ${photoRes.description}`;
          await tgRequest("sendMessage", { chat_id: chatId, text: statusText, reply_markup: getMainKeyboard() });
          return new Response("OK");
        }
      }

      // Upwork Taklif or /pitch
      if (text === "💼 Upwork Taklif" || text.startsWith("/pitch")) {
        const p = posts["01"];
        const pitch = `💼 <b>Upwork Uchun Maxsus Taklif Xati:</b>\n\n` +
          `<code>Hi! I saw your requirements and can deliver this cleanly and on schedule.\n\n` +
          `I have already architected and shipped a production build with this exact architecture: "${p.title}".\n` +
          `Live verified demo: ${BASE_URL}/${p.folder}/index.html\n\n` +
          `I ensure zero technical debt, modular components, and 30-day post-launch warranty. Let's discuss your roadmap!</code>`;
        await tgRequest("sendMessage", { chat_id: chatId, text: pitch, parse_mode: "HTML", reply_markup: getMainKeyboard() });
        return new Response("OK");
      }

      // Status
      if (text === "📊 Tizim Holati" || text.startsWith("/status")) {
        const status = `📊 <b>Cloudflare Serverless Tizim Holati:</b>\n\n` +
          `• Rejim: <b>Cloudflare Workers 24/7 Serverless</b>\n` +
          `• Jami loyihalar: <b>66 ta</b>\n` +
          `• Ulangan kanal: <b>${CHANNEL_ID}</b>\n` +
          `• Uptime: <b>99.99% (Kompyuter o'chiq bo'lsa ham ishlaydi)</b>\n` +
          `• Global Havola: <code>${BASE_URL}</code>`;
        await tgRequest("sendMessage", { chat_id: chatId, text: status, parse_mode: "HTML", reply_markup: getMainKeyboard() });
        return new Response("OK");
      }

      // Jonli Portfolio
      if (text === "🌐 Jonli Portfolio") {
        await tgRequest("sendMessage", {
          chat_id: chatId,
          text: `🌐 <b>Jonli Portfoliongiz:</b>\n\n` +
            `• <b>Master Hub:</b> ${BASE_URL}/index.html\n` +
            `• <b>Flagship Studio:</b> ${BASE_URL}/flagship/index.html\n` +
            `• <b>Barcha 66 ta loyiha faol!</b>`,
          parse_mode: "HTML",
          reply_markup: getMainKeyboard()
        });
        return new Response("OK");
      }

      // Default Response
      await tgRequest("sendMessage", {
        chat_id: chatId,
        text: `Buyruq qabul qilindi. Kanalga loyiha chiqarish uchun <b>"📢 Navbatdagi Loyiha"</b> tugmasini bosing!`,
        parse_mode: "HTML",
        reply_markup: getMainKeyboard()
      });

      return new Response("OK");
    } catch (err) {
      return new Response("Error: " + err.message, { status: 500 });
    }
  },

  // 3. Cron Trigger: Scheduled Auto-Posting
  async scheduled(event, env, ctx) {
    const keys = Object.keys(posts);
    const randKey = keys[Math.floor(Math.random() * keys.length)];
    const p = posts[randKey];
    if (p) {
      const postText = p.text.replace("https://ceo-portfolio.pages.dev", BASE_URL);
      await tgRequest("sendPhoto", {
        chat_id: CHANNEL_ID,
        photo: p.img,
        caption: postText
      });
    }
  }
};
