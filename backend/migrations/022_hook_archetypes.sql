-- The 11 hook archetypes, written the way a short-form editor speaks: 3 to 9 words once {x} (a 3-word gist of the clip) is
-- filled in, no dashes, colons or quotation marks (hook_rules enforces the same on every hook). Arabic is spoken (Levantine /
-- plain), not formal. Rows are data: the owner's results re-weight them per profile (hook_pattern_stats).
insert into hook_patterns (name, language, description, template, hook_type) values
 ('question', 'en', 'A question the clip answers, specific enough that the viewer wants the answer.', 'What if {x}?', 'question'),
 ('shocking_number', 'en', 'Open on a surprising, exact number from the clip.', '3 things nobody says about {x}', 'number'),
 ('nobody_tells_you', 'en', 'Promise something nobody tells people, then deliver it.', 'Nobody tells you this about {x}', 'contrarian'),
 ('bold_opinion', 'en', 'State a clear, bold opinion the clip backs up.', 'Unpopular opinion about {x}', 'contrarian'),
 ('story_opening', 'en', 'Start a story at its most tense moment.', 'It all started with {x}', 'story-open'),
 ('mistake_confession', 'en', 'Admit a real mistake the clip explains.', 'I was wrong about {x}', 'story-open'),
 ('stakes_warning', 'en', 'Say what the viewer loses by ignoring this.', 'Skip this and it costs you', 'stakes'),
 ('direct_you', 'en', 'Speak straight to the viewer about something they do.', 'You are doing {x} wrong', 'contrarian'),
 ('not_what_you_think', 'en', 'Flip a belief the viewer holds; the clip shows the truth.', '{x} is not what you think', 'contrarian'),
 ('result_first', 'en', 'Give the result first, then the clip shows how.', 'This changed how I see {x}', 'story-open'),
 ('insider_secret', 'en', 'Something people in the field know and outsiders do not.', 'What pros know about {x}', 'question'),
 ('question', 'ar', 'سؤال يجيب عنه المقطع، محدد بما يكفي ليريد المشاهد الجواب.', 'شو لو {x}؟', 'question'),
 ('shocking_number', 'ar', 'ابدأ برقم مفاجئ ودقيق من المقطع.', '3 أشياء ما حدا بقولها عن {x}', 'number'),
 ('nobody_tells_you', 'ar', 'ابدأ بوعد بشيء لا يقوله أحد ثم قدمه.', 'ما حدا بقلك هاد عن {x}', 'contrarian'),
 ('bold_opinion', 'ar', 'رأي جريء وواضح يدعمه المقطع.', 'رأيي بـ{x} مش شعبي', 'contrarian'),
 ('story_opening', 'ar', 'ابدأ القصة من لحظتها الأكثر توتراً.', 'كل شي بلش مع {x}', 'story-open'),
 ('mistake_confession', 'ar', 'اعتراف بخطأ حقيقي يشرحه المقطع.', 'كنت غلطان بموضوع {x}', 'story-open'),
 ('stakes_warning', 'ar', 'قل ماذا يخسر المشاهد إن تجاهل هذا.', 'تجاهل هاد وبتدفع الثمن', 'stakes'),
 ('direct_you', 'ar', 'خاطب المشاهد مباشرة عن شي بعمله.', 'إنت بتعمل {x} غلط', 'contrarian'),
 ('not_what_you_think', 'ar', 'اقلب فكرة يؤمن بها المشاهد؛ المقطع يوريه الحقيقة.', '{x} مش متل ما بتفكر', 'contrarian'),
 ('result_first', 'ar', 'ابدأ بالنتيجة ثم يعرض المقطع الطريقة.', 'هاد غيّر نظرتي لـ{x}', 'story-open'),
 ('insider_secret', 'ar', 'شي يعرفه أهل المجال ولا يعرفه غيرهم.', 'شو بيعرف المحترفين عن {x}', 'question')
on conflict (name, language) do update set description = excluded.description, template = excluded.template, hook_type = excluded.hook_type;
