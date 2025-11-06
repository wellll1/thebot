import telegram
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue
import requests
import re
from datetime import datetime, timedelta 
import wikipedia
import unicodedata 
import asyncio 

# ==============================================================================
# 1. الإعدادات والتوكن 
# ==============================================================================
TOKEN = '8591832490:AAHZaVrJTyyIxDTrwuGiRnQe5zsYXve2c7c' 

current_articles = {} 
DEFAULT_WORD_LIMIT = 150 
DEFAULT_BOT_SPEED = 0 
DEFAULT_NEXT_ROUND_DELAY = 3 

# ==============================================================================
# 2. دالة التوحيد الأكثر مرونة للمقارنة (المُحدَّثة للتعامل مع الفاصلة بشكل قاطع)
# ==============================================================================
def normalize_for_comparison(text):
    """
    تنظف النص وتوحد الحروف البديلة وتزيل علامات الترقيم والأحرف غير العربية
    لضمان مرونة المقارنة النهائية.
    """
    
    text = str(text)
    text = unicodedata.normalize('NFKC', text) # توحيد يونيكود

    # 🚨 التعديل القاطع: إزالة الفاصلة والنقطة والفاصلة المنقوطة صراحةً.
    # هذا يضمن "تحويل الفاصلة إلى لا شيء" في كل الظروف.
    text = re.sub(r'[,.،؛]', '', text) 

    # 1. توحيد الألفات والهمزات والرقم '1' إلى ألف مجردة (ا)
    text = re.sub(r'[أإآء1]', 'ا', text)
    
    # 2. توحيد الياء غير المنقوطة (ى) إلى ياء منقوطة (ي)
    text = re.sub(r'[ى]', 'ي', text) 
    
    # 3. توحيد التاء المربوطة (ة) إلى هاء (ه)
    text = re.sub(r'[ة]', 'ه', text)
    
    # 4. توحيد الواو المهموزة (ؤ) إلى واو عادية (و)
    text = re.sub(r'[ؤ]', 'و', text) 

    # 5. إزالة التشكيل (الحركات) والوصلة المطولة (ـ)
    text = re.sub(r'[\u064b-\u0652\u0640]', '', text) 
    
    # 6. إزالة كافة الأحرف غير العربية والمسافات المتبقية (مثل علامات الترقيم الأخرى أو الأحرف اللاتينية)
    cleaned_text = re.sub(r'[^\u0600-\u06FF\s]', '', text)

    # 7. توحيد المسافات: إزالة المسافات المتعددة والزائدة.
    cleaned_text = ' '.join(cleaned_text.split()).strip()
    
    return cleaned_text

# ==============================================================================
# 3. وظيفة جلب وتنقية المقال (للعرض)
# ==============================================================================
def get_and_clean_arabic_wiki_article(word_limit=DEFAULT_WORD_LIMIT): 
    wikipedia.set_lang("ar")
    
    try:
        random_title = wikipedia.random(pages=1)
        page = wikipedia.page(random_title, auto_suggest=False)
        full_text = page.content

    except (wikipedia.exceptions.PageError, wikipedia.exceptions.RedirectError, Exception) as e:
        print(f"خطأ في جلب المقال: {e}. نحاول مجدداً.")
        return get_and_clean_arabic_wiki_article(word_limit) 

    # 3.1. إزالة الأقسام الختامية والروابط الخارجية
    keywords_to_remove = r'(==\s*([^\n]+?)\s*==.*)|(مراجع|وصلات خارجية|انظر أيضا|بوابة|تصنيف|طالع أيضاً|مصادر)\s*.*'
    cleaned_full_text = re.sub(keywords_to_remove, '', full_text, flags=re.IGNORECASE | re.DOTALL)
    
    # 3.2. التنقية الشاملة: إزالة التشكيل، الأرقام، الأحرف اللاتينية، والرموز
    
    # إزالة التشكيل أولاً
    article_for_display = re.sub(r'[\u064b-\u0652\u0640]', '', cleaned_full_text)
    
    # إزالة الأرقام والأحرف اللاتينية وكل الرموز الأخرى عدا الحروف العربية والمسافات
    article_for_display = re.sub(r'[^\u0600-\u06FF\s]', '', article_for_display)

    # 3.3. إزالة المسافات المتعددة وتنقية النص ثم اختيار الجزء المحدد بالـ word_limit
    words = ' '.join(article_for_display.split()).split()
    article_to_send = ' '.join(words[:word_limit]).strip() 
    
    if not article_to_send:
        print("النص بعد التنقية قصير جداً. نحاول مجدداً.")
        return get_and_clean_arabic_wiki_article(word_limit) 
        
    return article_to_send

# ==============================================================================
# 4. معالج الأمر لتحديد عدد الكلمات
# ==============================================================================
async def set_word_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يحدد عدد الكلمات المطلوب ويخزنه دون بدء الاختبار."""
    user_message = update.message.text
    match = re.match(r'كلمات\s+(\d+)', user_message, re.IGNORECASE)
    
    if match:
        try:
            requested_limit = int(match.group(1))
            context.user_data['word_limit'] = requested_limit
            await update.message.reply_text("تم")
        except ValueError:
            await update.message.reply_text("عفواً، يجب أن يكون العدد رقماً صحيحاً.")
    else:
        await update.message.reply_text("صيغة خاطئة. الرجاء استخدام 'كلمات [العدد]'.")

# ==============================================================================
# 5. معالج الأمر لتحديد سرعة البوت المنافس 
# ==============================================================================
async def set_bot_speed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يحدد سرعة البوت المنافس (بالـ WPM) ويخزنها دون بدء الاختبار."""
    user_message = update.message.text
    match = re.match(r'سرعه\s+(\d+)', user_message, re.IGNORECASE)
    
    if match:
        try:
            requested_speed = int(match.group(1))
            context.user_data['bot_speed'] = requested_speed
            await update.message.reply_text("تم")
        except ValueError:
            await update.message.reply_text("عفواً، يجب أن تكون السرعة رقماً صحيحاً.")
    else:
        await update.message.reply_text("صيغة خاطئة. الرجاء استخدام 'سرعه [العدد]'.")

# ==============================================================================
# 6. معالج الأمر لتحديد زمن التأخير
# ==============================================================================
async def set_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يحدد مدة التأخير بين الجولات المستمرة (بالثواني) كأمر مستقل."""
    
    user_message = update.message.text
    match = re.match(r'تأخير\s+(\d+)', user_message, re.IGNORECASE)
    
    if match:
        try:
            requested_delay = int(match.group(1))
            context.user_data['next_round_delay'] = requested_delay
            await update.message.reply_text(f"تم تحديد زمن التأخير بين المقالات بنجاح ({requested_delay} ثوانٍ). يمكنك الآن بدء التنافس المستمر.")
        except ValueError:
            await update.message.reply_text("عفواً، يجب أن يكون زمن التأخير رقماً صحيحاً.")
    else:
        await update.message.reply_text("صيغة خاطئة. الرجاء استخدام 'تأخير [العدد بالثواني]'.")


# ==============================================================================
# 7. وظائف بدء وإدارة الجولات
# ==============================================================================

async def start_new_round(update: Update, context: ContextTypes.DEFAULT_TYPE, continuous_mode=False):
    """يبدأ جولة كتابة جديدة."""
    
    user_id = update.effective_user.id if update else context.job.user_id 
    chat_id = update.effective_chat.id if update else context.job.chat_id

    word_limit = context.user_data.get('word_limit', DEFAULT_WORD_LIMIT)
    bot_speed = context.user_data.get('bot_speed', DEFAULT_BOT_SPEED) 
    
    # إلغاء أي اختبار سابق لهذا المستخدم
    if user_id in current_articles:
        old_job_name = current_articles[user_id].get('bot_job_name')
        if old_job_name:
             current_jobs = context.job_queue.get_jobs_by_name(old_job_name)
             for job in current_jobs:
                 job.schedule_removal()
        del current_articles[user_id]
        
    await context.bot.send_message(chat_id=chat_id, text="#1233333333")
    
    article = get_and_clean_arabic_wiki_article(word_limit)
    
    if article:
        
        start_time = datetime.now() 
        
        current_articles[user_id] = {
            'text': article, 
            'start_time': start_time, 
            'bot_job_name': None,
            'continuous_mode': continuous_mode,
            'is_race_finished': False # حالة انتهاء الجولة لمنع التداخل
        }
        
        word_count = len(article.split())
        
        if bot_speed > 0:
            time_in_minutes = word_count / bot_speed
            bot_time_seconds = time_in_minutes * 60
            
            # إرسال مؤشر الكتابة (Typing Action)
            await context.bot.send_chat_action(chat_id=chat_id, action=telegram.constants.ChatAction.TYPING)
            
            # 🚨 المهمة 1: إرسال المقالة المكتوبة كاملة (تُنفذ دائماً بعد وقت البوت)
            context.job_queue.run_once(
                bot_send_article_only,
                bot_time_seconds, 
                chat_id=chat_id, 
                data={'article': normalize_for_comparison(article)},
            )

            # 🚨 المهمة 2: إرسال نتيجة البوت وجدولة الجولة التالية (هذه هي مهمة السباق القابلة للإلغاء)
            job = context.job_queue.run_once(
                bot_race_finish, 
                bot_time_seconds + 0.5, # تأخير بسيط لضمان وصول المقالة أولاً
                chat_id=chat_id, 
                user_id=user_id,
                data={
                    'word_count': word_count,
                    'start_time': start_time.isoformat(),
                    'continuous_mode': continuous_mode 
                }, 
                name=f"bot_race_finish_{user_id}"
            )
            
            current_articles[user_id]['bot_job_name'] = job.name
        
        # إرسال المقالة التي يجب على المستخدم كتابتها أولاً
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"{article}",
            parse_mode=telegram.constants.ParseMode.MARKDOWN
        )
    else:
        await context.bot.send_message(chat_id=chat_id, text="عذراً، لم نتمكن من جلب مقال مناسب حالياً.")
        if continuous_mode:
             await context.bot.send_message(chat_id=chat_id, text="تم إيقاف الوضع المستمر بسبب فشل جلب المقالة. أرسل 'الغاء' إذا لم يتوقف المؤقت.")


async def schedule_next_round(context: ContextTypes.DEFAULT_TYPE):
    """جدولة بدء الجولة التالية بعد تأخير بسيط."""
    
    job = context.job
    user_id = job.user_id
    chat_id = job.chat_id
    
    # حذف بيانات الجولة السابقة قبل بدء الجولة الجديدة
    if user_id in current_articles:
        del current_articles[user_id] 
        
    # بدء الجولة الجديدة
    await start_new_round(None, context, continuous_mode=True)


async def bot_send_article_only(context: ContextTypes.DEFAULT_TYPE):
    """وظيفة تُرسل المقالة التي كان يكتبها البوت دون حساب سرعته."""
    job = context.job
    article_to_send = job.data['article']
    chat_id = job.chat_id
    
    await context.bot.send_message(
        chat_id=chat_id, 
        text=article_to_send,
        parse_mode=telegram.constants.ParseMode.MARKDOWN
    )

async def bot_race_finish(context: ContextTypes.DEFAULT_TYPE):
    """يرسل سرعة البوت المنافس المحسوبة بدقة ويُنهي الجولة."""
    job = context.job
    chat_id = job.chat_id
    user_id = job.user_id
    data = job.data 
    word_count = data['word_count']
    start_time_str = data['start_time'] 
    continuous_mode = data['continuous_mode'] 
    
    if user_id in current_articles:
        
        # 🚨 التحقق من حالة الانتهاء: إذا انتهت، نخرج دون أي إجراء (الفائز يأخذ كل شيء)
        if current_articles[user_id]['is_race_finished']:
            return 

        # 1. حساب سرعة البوت
        start_time = datetime.fromisoformat(start_time_str) 
        end_time = datetime.now()
        time_difference = (end_time - start_time).total_seconds()
        
        bot_wpm = 0
        if time_difference > 0:
            time_in_minutes = time_difference / 60
            bot_wpm = round(word_count / time_in_minutes)
        else:
            bot_wpm = "سريع جداً!" 
            
        
        # 2. إرسال سرعة البوت المنافس المحسوبة (تحدث فقط إذا فاز البوت)
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"wpm : {bot_wpm}"
        )
        
        # 3. تعيين حالة الانتهاء (البوت أنهى أولاً)
        current_articles[user_id]['is_race_finished'] = True
        
        # 4. جدولة الجولة التالية إذا كان الوضع مستمراً
        if continuous_mode:
            
            delay = context.user_data.get('next_round_delay', DEFAULT_NEXT_ROUND_DELAY) 
            
            context.job_queue.run_once(
                schedule_next_round,
                delay, 
                chat_id=chat_id,
                user_id=user_id,
                name=f"next_round_{user_id}"
            )
        else:
            # إذا لم يكن الوضع مستمراً، نحذف مباشرة.
            del current_articles[user_id]


# ==============================================================================
# 8. معالج الأمر "تلقائي" (لبدء اختبار الجولة الواحدة)
# ==============================================================================
async def start_typing_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يبدأ اختبار الكتابة لمرة واحدة."""
    await start_new_round(update, context, continuous_mode=False)


# ==============================================================================
# 9. معالج الأمر "مستمر" (لبدء اختبار الجولات المستمرة) 
# ==============================================================================
async def start_continuous_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يبدأ اختبار الكتابة المستمر، ويسأل عن التأخير إذا لم يتم تحديده مسبقاً."""
    
    user_id = update.effective_user.id
    
    if 'next_round_delay' not in context.user_data:
        # تعيين حالة الانتظار قبل طرح السؤال
        context.user_data['waiting_for_delay'] = True
        
        await update.message.reply_text(
            "كم تبي ما بين كل مقالة ومقالة ؟"
        )
        return
        
    # إذا تم تحديد التأخير، يبدأ التنافس
    await start_new_round(update, context, continuous_mode=True)


# ==============================================================================
# 10. معالج الأمر "الغاء" (لإيقاف الجولات المستمرة) 
# ==============================================================================
async def cancel_continuous_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يلغي أي اختبار جاري أو وضع مستمر لهذا المستخدم."""
    
    user_id = update.effective_user.id
    
    if 'waiting_for_delay' in context.user_data:
        del context.user_data['waiting_for_delay']
        
    chat_id = update.effective_chat.id
    
    if user_id in current_articles:
        
        # 1. إلغاء مهمة البوت المنافس (الخاصة بالسباق والـ WPM)
        job_name = current_articles[user_id].get('bot_job_name')
        if job_name:
            current_jobs = context.job_queue.get_jobs_by_name(job_name)
            for job in current_jobs:
                job.schedule_removal()
        
        # 2. إلغاء مهمة الجولة التالية (إذا كانت قيد الجدولة)
        next_round_jobs = context.job_queue.get_jobs_by_name(f"next_round_{user_id}")
        for job in next_round_jobs:
            job.schedule_removal()

        # 3. إزالة بيانات الجولة
        del current_articles[user_id]
        
        # تم حذف: await update.message.reply_text("تم إلغاء الوضع المستمر والجولة الحالية بنجاح.")
    else:
        # تم حذف: await update.message.reply_text("لا توجد جولة جارية حالياً لإلغائها.")
        pass # لا نرسل شيئًا إذا لم تكن هناك جولة جارية.


# ==============================================================================
# 11. معالج رسائل المستخدمين (للتصحيح وحساب السرعة / إدخال التأخير) 
# ==============================================================================
async def check_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يفحص إجابة المستخدم ويحسب السرعة أو يحفظ زمن التأخير."""
    
    user_id = update.effective_user.id
    user_message = update.message.text
    
    # الأولوية 1: التحقق مما إذا كنا ننتظر إدخال التأخير
    if context.user_data.get('waiting_for_delay', False):
        try:
            requested_delay = int(user_message.strip())
            if requested_delay < 0:
                raise ValueError
                
            context.user_data['next_round_delay'] = requested_delay
            del context.user_data['waiting_for_delay']
            
            # تم حذف: await update.message.reply_text(f"تم تحديد التأخير لـ {requested_delay} ثوانٍ. بدء التنافس المستمر!")
            
            # يتم بدء الجولة فوراً بعد تحديد التأخير بنجاح
            await start_new_round(update, context, continuous_mode=True) 
            return
            
        except ValueError:
            # تم حذف: await update.message.reply_text("الإدخال غير صالح. الرجاء إرسال رقم صحيح يمثل عدد الثواني.")
            return

    # الأولوية 2: معالجة إجابة اختبار الكتابة
    if user_id in current_articles:
        
        expected_text = current_articles[user_id]['text']
        continuous_mode = current_articles[user_id]['continuous_mode'] 
        
        purified_user_text = normalize_for_comparison(user_message)
        purified_expected_text = normalize_for_comparison(expected_text)

        # التحقق من صحة النص أولاً (يتم الحساب فقط إذا كان النص صحيحاً)
        if purified_user_text == purified_expected_text:
            
            # 🚨 التحقق من حالة الانتهاء: إذا انتهت، نخرج دون أي إجراء (الفائز يأخذ كل شيء)
            if current_articles[user_id]['is_race_finished']:
                return 

            # 1. حساب سرعة المستخدم
            start_time = current_articles[user_id]['start_time']
            end_time = datetime.now()
            time_difference = (end_time - start_time).total_seconds()
            word_count = len(expected_text.split())
            user_wpm = round(word_count / (time_difference / 60)) if time_difference > 0 else "سريع جداً!" 
            
            # الرد بالنتيجة (يحدث فقط إذا لم يكن is_race_finished = True)
            result_message = f"wpm : {user_wpm}"
            await update.message.reply_text(result_message)

            # 2. إلغاء مهمة البوت المنافس (الخاصة بالسباق/الـ WPM)
            job_name = current_articles[user_id].get('bot_job_name')
            if job_name:
                current_jobs = context.job_queue.get_jobs_by_name(job_name)
                for job in current_jobs:
                    job.schedule_removal()
            
            # 3. تعيين حالة الانتهاء والجدولة (المستخدم فاز أولاً)
            current_articles[user_id]['is_race_finished'] = True
            
            if continuous_mode:
                delay = context.user_data.get('next_round_delay', DEFAULT_NEXT_ROUND_DELAY) 
                
                context.job_queue.run_once(
                    schedule_next_round,
                    delay, 
                    chat_id=update.effective_chat.id,
                    user_id=user_id,
                    name=f"next_round_{user_id}"
                )
            else:
                # إذا لم يكن الوضع مستمراً، نحذف مباشرة.
                del current_articles[user_id]
        
        # إذا لم تكن الإجابة صحيحة، يتم تجاهلها وعدم القيام بأي شيء.


# ==============================================================================
# 12. وظيفة تشغيل البوت الرئيسية
# ==============================================================================
def main():
    """تشغيل البوت."""
    
    application = Application.builder().token(TOKEN).build()
    job_queue = application.job_queue

    application.add_handler(CommandHandler("start", lambda update, context: update.message.reply_text("ارحب")))

    # الإعدادات
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(re.compile(r'^كلمات\s+\d+$', re.IGNORECASE)), set_word_limit))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(re.compile(r'^سرعه\s+\d+$', re.IGNORECASE)), set_bot_speed))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(re.compile(r'^تأخير\s+\d+$', re.IGNORECASE)), set_delay)) 
    
    # أوضاع التشغيل
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(re.compile(r'^تلقائي$', re.IGNORECASE)), start_typing_test))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(re.compile(r'^مستمر$', re.IGNORECASE)), start_continuous_test)) 
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(re.compile(r'^الغاء$', re.IGNORECASE)), cancel_continuous_test)) 

    # معالج الإجابات
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND) & ~filters.Regex(re.compile(r'^كلمات|سرعه|تلقائي|مستمر|الغاء|تأخير$', re.IGNORECASE)), check_user_input))

    print("البوت يعمل بنجاح...")
    application.run_polling(poll_interval=1.0)

if __name__ == '__main__':
    main()
