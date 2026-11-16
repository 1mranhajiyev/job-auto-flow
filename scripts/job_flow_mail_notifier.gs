/**
 * Job Flow Mail Notifier
 *
 * Gələn qutudakı oxunmamış məktubları yoxlayır. Müsahibə dəvəti, texniki
 * tapşırıq, HR cavabı və ya imtina varsa JobFlow Telegram botuna göndərir.
 *
 * Tokenləri koda yazmayın. Apps Script → Project Settings → Script properties:
 *   TELEGRAM_BOT_TOKEN
 *   TELEGRAM_CHAT_ID
 *   GEMINI_API_KEY
 *
 * Bir dəfə installTrigger() işə salın. Sonra hər 15 dəqiqədən bir yoxlanır.
 */

var MODEL = "gemini-2.5-flash";
var SKIP_LABEL = "JobFlow/yox";

function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === "checkAllUnreadJobReplies") {
      ScriptApp.deleteTrigger(trigger);
    }
  });
  ScriptApp.newTrigger("checkAllUnreadJobReplies")
    .timeBased()
    .everyMinutes(15)
    .create();
}

function checkAllUnreadJobReplies() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;

  try {
    var props = PropertiesService.getScriptProperties();
    var token = props.getProperty("TELEGRAM_BOT_TOKEN");
    var chatId = props.getProperty("TELEGRAM_CHAT_ID");
    var apiKey = props.getProperty("GEMINI_API_KEY");
    if (!token || !chatId || !apiKey) {
      console.error("Script properties tamam deyil.");
      return;
    }

    var me = String(Session.getActiveUser().getEmail() || "").toLowerCase();
    var skipLabel = GmailApp.getUserLabelByName(SKIP_LABEL) || GmailApp.createLabel(SKIP_LABEL);
    var threads = GmailApp.search(
      "in:inbox is:unread newer_than:1d -category:promotions -category:social -label:" + SKIP_LABEL,
      0,
      20
    );

    threads.forEach(function (thread) {
      var messages = thread.getMessages();
      var last = messages[messages.length - 1];
      if (!last || last.isDraft()) return;

      var sender = last.getFrom() || "";
      if (me && sender.toLowerCase().indexOf(me) !== -1) return;

      var subject = last.getSubject() || "(mövzusuz)";
      var body = last.getPlainBody().substring(0, 1500);
      var verdict = analyzeEmailWithGemini(apiKey, sender, subject, body);
      if (!verdict) return;

      if (!verdict.yes) {
        thread.addLabel(skipLabel);
        return;
      }

      var text =
        "📩 <b>Yeni vakansiya cavabı</b>\n\n" +
        "👤 <b>Kimdən:</b> " + escapeHtml(sender) + "\n" +
        "📌 <b>Mövzu:</b> " + escapeHtml(subject) + "\n\n" +
        "🤖 <b>Xülasə:</b>\n" + escapeHtml(verdict.summary) + "\n\n" +
        '<a href="' + escapeHtml(thread.getPermalink()) + '">Maili aç</a>';

      if (sendTelegramMessage(token, chatId, text)) {
        thread.markRead();
      }
      Utilities.sleep(400);
    });
  } finally {
    lock.releaseLock();
  }
}

function analyzeEmailWithGemini(apiKey, sender, subject, body) {
  var url = "https://generativelanguage.googleapis.com/v1beta/models/" +
    MODEL + ":generateContent?key=" + encodeURIComponent(apiKey);
  var prompt =
    "Sən iş müraciətlərini analiz edən köməkçisən. Aşağıdakı email istifadəçinin " +
    "göndərdiyi vakansiya müraciətinə gələn REAL cavabdırsa (müsahibə dəvəti, " +
    "texniki tapşırıq, müraciətin qəbulu, HR cavabı və ya imtina), cavabı " +
    '"YES: " ilə başla və 1-2 cümlə ilə Azərbaycan dilində qısa xülasə yaz. ' +
    "Əgər bu GitHub OAuth, avtomatik vakansiya bülleteni, reklam, bank və ya " +
    'kripto bildirişi, öz göndərdiyin məktub və ya başqa mövzudursa, yalnız "NO" yaz.\n\n' +
    "Kimdən: " + sender + "\nMövzu: " + subject + "\nMətn: " + body;

  var res = UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] }),
    muteHttpExceptions: true
  });

  if (res.getResponseCode() !== 200) {
    console.error("Gemini cavabı: " + res.getResponseCode());
    return null;
  }

  var json = JSON.parse(res.getContentText());
  var text = (((json.candidates || [])[0] || {}).content || {}).parts;
  text = text && text[0] ? String(text[0].text || "") : "";
  text = text.replace(/```[a-z]*/gi, "").replace(/```/g, "").trim();

  if (/^YES\b/i.test(text)) {
    return {
      yes: true,
      summary: text.replace(/^YES\s*:?\s*/i, "").substring(0, 500) || "HR cavabı gəlib."
    };
  }
  if (/^NO\b/i.test(text)) return { yes: false };
  return null;
}

function sendTelegramMessage(token, chatId, text) {
  var res = UrlFetchApp.fetch("https://api.telegram.org/bot" + token + "/sendMessage", {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({
      chat_id: chatId,
      text: text,
      parse_mode: "HTML",
      disable_web_page_preview: true
    }),
    muteHttpExceptions: true
  });
  return res.getResponseCode() === 200;
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
