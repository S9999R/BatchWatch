# BatchWatch: demo video script (under 3 minutes)

Rules from the hackathon: under 3 minutes, public on YouTube, show the project working, and say
**Nebius Token Factory** and **NVIDIA Nemotron** out loud (not only on screen). No copyrighted music.

Narration is in English for the judges, about 400 words (roughly 2:50 at a calm pace).
"On screen" says what to show while the line is read. Record the site in English; switch to Arabic once near the end.

---

## 1. The problem (0:00 – 0:20)

**On screen:** the home page, empty, "Is this medicine recalled?"

> In October 2025, a contaminated cough syrup killed children in India, and pharmacies were asked to check their shelves by hand.
> In April 2026, Pakistan recalled four batches of a medicine. Nigeria's regulator asked its staff to search for the same batches 126 days later.
> A batch recalled in one country is often still on the shelf in another.

## 2. What BatchWatch is (0:20 – 0:30)

**On screen:** cursor in the search box, then over the examples.

> BatchWatch answers one question in seconds: is this medicine recalled?
> It watches the recall and falsified-medicine alerts of 10 drug regulators, from Saudi Arabia's SFDA to the FDA, the MHRA and the WHO.

## 3. One pack (0:30 – 1:05)

**On screen:** click **Barcode of a recalled pack** (or scan `demo-pack.svg` with a phone). The red **Stop dispensing** card appears.

> A pharmacist scans the barcode on the box. It is decoded on the phone, and the answer is plain: stop dispensing.
> What to do, the regulator's own instruction, and a quarantine note ready to copy.

**On screen:** click **AVT50**. Show the two regulators and the dates.

> This one is a falsified cancer medicine, reported by the WHO and, a month later, by Pakistan.

**On screen:** click **25JO1305**. Show the character comparison and the Yes / No question.

> And this batch number differs from a recalled one only by a letter O and a zero. BatchWatch does not guess: it asks the pharmacist to check.

## 4. A whole stock list (1:05 – 1:25)

**On screen:** **Stock list** tab → **Riyadh pharmacy · 16 items**. Show "9 of 16 stock lines need action", then **Download report (CSV)**.

> A pharmacy can upload its whole stock export, Arabic or English columns. Nine of these sixteen lines need action. The file is checked in memory and never stored.

## 5. How it works: Nebius and NVIDIA (1:25 – 2:15)

**On screen:** the **How it works** page, the six steps.

> Every new alert is read by NVIDIA Nemotron 3 Nano, served on Nebius Token Factory, into batch-level records as structured JSON.
> Six rule-based checks then compare that reading with the regulator's own text. If a check fails, Nemotron 3 Super reads the alert again.
> Matching a pack is pure rules: instant, free and explainable.

**On screen:** **Alerts** page → search `104433` → open the SFDA Credanil recall (alert `SFDA-19404`) → show the verified finding from Botswana, 36 days later. Then press **Run the agent again, live** and let the steps appear (about 30–40 s; cut the wait in editing).

> Finally, an agent on Nemotron 3 Super, using Tavily search, decides its own searches to find where else a batch was flagged.
> This Saudi recall turned up in Botswana 36 days later. A finding counts only if its quote and batch numbers are really on the page it read.

*(Optional, if there is credit: show a terminal running `python -m batchwatch.watch` reading new alerts with Nemotron on Token Factory, about $0.05.)*

## 6. Results (2:15 – 2:40)

**On screen:** the four numbers at the top of **How it works**, then the model table.

> We measured it on 90 real alerts with every batch number labelled by hand.
> 90 out of 90 alerts correct, all 340 batch numbers found, none invented, for under 8 cents.
> That is the accuracy of Nemotron 3 Ultra at about an eighth of the cost. Token Factory's single API made switching between Nano, Super and Ultra a one-line change.

## 7. Close (2:40 – 2:55)

**On screen:** press **ع** to show the Arabic interface, then back to the home page.

> BatchWatch works in English and Arabic. It never calls a medicine safe, and it never quarantines stock on its own: the pharmacist decides.
> BatchWatch: built with NVIDIA Nemotron on Nebius Token Factory.

---

## تعليمات التسجيل

1. **قبل التسجيل:** حط مفتاح الحساب الجديد في `.env`، وشغّل الموقع، وافتحه بالإنجليزي على شاشة كاملة 1920×1080. سكّر أي تبويبات أو إشعارات ممكن تطلع في الفيديو.
2. **التسجيل:** افتح **Clipchamp** (موجود في ويندوز 11) ← Record ← Screen. سجّل الشاشة أول، وبعدين سجّل صوتك فوقها مقطع مقطع. هذي الطريقة أسهل من التسجيل مرة وحدة.
3. **الصوت:** اقرأ النص بهدوء. لازم تنطق "Nebius Token Factory" و"NVIDIA Nemotron" بوضوح، لأن المحكّمين يدورون عليها.
4. **الطول:** إذا طلع أطول من 2:55، احذف من المشهد 3 أو 4 أول، ولا تحذف من المشهد 5 أو 6.
5. **الرفع:** على يوتيوب بإعداد **Public**، والعنوان مثلاً: *BatchWatch: is this medicine recalled? (NVIDIA Nemotron on Nebius Token Factory)*.
6. **ممنوع** أي موسيقى عليها حقوق. إذا تبي موسيقى خلفية، خذها من مكتبة Clipchamp المجانية.
