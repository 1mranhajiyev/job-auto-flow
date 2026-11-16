/**
 * Job Flow Mail Notifier
 *
 * Məktubu qəbul edən Gmail hesabında işləyir. Hər gün bir dəfə həmin günün
 * oxunmamış məktublarına baxır. Müsahibə dəvəti, texniki tapşırıq, HR cavabı
 * və ya imtina varsa JobFlow Telegram botuna göndərir.
 *
 * Tokenləri koda yazmayın. Apps Script → Project Settings → Script properties:
 *   TELEGRAM_BOT_TOKEN
 *   TELEGRAM_CHAT_ID
 *   GEMINI_API_KEY
 *
 * Bir dəfə installTrigger() işə salın. Sonra hər gün saat 21:00 civarında yoxlanır.
 */

var MODEL = "gemini-2.5-flash";
var SKIP_LABEL = "JobFlow/yox";
var SENT_LABEL = "JobFlow/gonderildi";
var HOUR = 21;
var TIMEZONE = "Asia/Baku";
var LAST_ERROR = "";

function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === "checkAllUnreadJobReplies") {
      ScriptApp.deleteTrigger(trigger);
    }
  });
  ScriptApp.newTrigger("checkAllUnreadJobReplies")
    .timeBased()
    .atHour(HOUR)
    .everyDays(1)
    .inTimezone(TIMEZONE)
    .create();
}

function todayQuery() {
  var now = new Date();
  var after = Utilities.formatDate(new Date(now.getTime() - 24 * 60 * 60 * 1000), TIMEZONE, "yyyy/MM/dd");
  var before = Utilities.formatDate(new Date(now.getTime() + 24 * 60 * 60 * 1000), TIMEZONE, "yyyy/MM/dd");
  return 'is:unread after:' + after + ' before:' + before +
    ' -in:sent -in:draft -in:chats -label:"' + SKIP_LABEL + '" -label:"' + SENT_LABEL + '"';
}

function checkAllUnreadJobReplies() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;

  try {
    var props = PropertiesService.getScriptProperties();
    var token = props.getProperty("TELEGRAM_BOT_TOKEN");
    var chatId = props.getProperty("TELEGRAM_CHAT_ID");
    var apiKey = props.getProperty("GEMINI_API_KEY");
    var missing = [];
    if (!token) missing.push("TELEGRAM_BOT_TOKEN");
    if (!chatId) missing.push("TELEGRAM_CHAT_ID");
    if (!apiKey) missing.push("GEMINI_API_KEY");
    if (missing.length) {
      var missingText = "Script properties tamam deyil: " + missing.join(", ");
      console.error(missingText);
      if (token && chatId) {
        sendTelegramMessage(token, chatId, "⚠️ Poçt yoxlaması alınmadı.\n" + escapeHtml(missingText));
      }
      return;
    }

    var me = String(Session.getActiveUser().getEmail() || "").toLowerCase();
    var skipLabel = GmailApp.getUserLabelByName(SKIP_LABEL) || GmailApp.createLabel(SKIP_LABEL);
    var sentLabel = GmailApp.getUserLabelByName(SENT_LABEL) || GmailApp.createLabel(SENT_LABEL);
    var query = todayQuery();
    var threads = GmailApp.search(query, 0, 30);
    console.log(query);
    console.log("Hesab: " + (me || "naməlum") + ", tapılan: " + threads.length);
    var problems = [];

    threads.forEach(function (thread) {
      var messages = thread.getMessages();
      var last = messages[messages.length - 1];
      if (!last || last.isDraft()) return;

      var sender = last.getFrom() || "";
      var subject = last.getSubject() || "(mövzusuz)";
      if (me && senderEmail(sender) === me) {
        console.log("Öz ünvanın keçildi: " + subject);
        return;
      }

      var body = last.getPlainBody().substring(0, 1500);
      var verdict = analyzeEmailWithGemini(apiKey, sender, subject, body);
      if (!verdict) {
        problems.push(subject + ": " + (LAST_ERROR || "model cavab vermədi"));
        return;
      }

      if (!verdict.yes) {
        thread.addLabel(skipLabel);
        console.log("HR cavabı deyil: " + subject);
        return;
      }
      console.log("HR cavabı: " + subject);

      var text =
        "📩 <b>Yeni vakansiya cavabı</b>\n\n" +
        "👤 <b>Kimdən:</b> " + escapeHtml(sender) + "\n" +
        "📌 <b>Mövzu:</b> " + escapeHtml(subject) + "\n\n" +
        "🤖 <b>Xülasə:</b>\n" + escapeHtml(verdict.summary) + "\n\n" +
        '<a href="' + escapeHtml(thread.getPermalink()) + '">Maili aç</a>';

      if (sendTelegramMessage(token, chatId, text)) {
        thread.addLabel(sentLabel);
        thread.markRead();
        console.log("Telegrama getdi: " + subject);
      } else {
        problems.push(subject + ": " + (LAST_ERROR || "Telegram alınmadı"));
      }
      Utilities.sleep(400);
    });
    if (problems.length) {
      sendTelegramMessage(
        token,
        chatId,
        "⚠️ Poçt yoxlaması alınmadı.\n" + escapeHtml(problems.slice(0, 5).join("\n"))
      );
    }
  } catch (err) {
    console.error(err);
    if (token && chatId) {
      sendTelegramMessage(token, chatId, "⚠️ Poçt yoxlaması alınmadı.\n" + escapeHtml(String(err).substring(0, 400)));
    }
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
    'kripto bildirişi və ya başqa mövzudursa, yalnız "NO" yaz. ' +
    "Göndərənin adı istifadəçiyə bənzəsə və ya məktub onun başqa şəxsi ünvanından " +
    "test üçün gəlsə belə, mətndə müsahibə dəvəti varsa YES yaz.\n\n" +
    "Kimdən: " + sender + "\nMövzu: " + subject + "\nMətn: " + body;

  var res = UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] }),
    muteHttpExceptions: true
  });

  var code = res.getResponseCode();
  if (code !== 200) {
    LAST_ERROR = "Gemini " + code;
    console.error("Gemini: " + code + " " + res.getContentText().substring(0, 180));
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
  var code = res.getResponseCode();
  if (code !== 200) {
    LAST_ERROR = "Telegram " + code;
    console.error("Telegram: " + code + " " + res.getContentText().substring(0, 180));
    return false;
  }
  return true;
}

function senderEmail(from) {
  var match = String(from || "").toLowerCase().match(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/);
  return match ? match[0] : "";
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
