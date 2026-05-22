INSERT INTO scenarios (id, title, target_language, base_language, system_prompt, difficulty_level)
VALUES 
(
  gen_random_uuid(),
  'Asking for Directions in Madrid',
  'es-ES',
  'en-US',
  'Lesson type: beginner guided lesson.
Lesson objective: asking for directions and finding places.
Vocabulary targets (teach in this order): el baño (bathroom), la calle (street), el museo (museum), el hotel (hotel), el tren (train).
Phrase targets (teach in this order): "¿Dónde está ...?", "A la derecha", "A la izquierda".
Progression: start in English. For each target, explain in English, give the Spanish, ask the learner to repeat. Do not move on until mastery (2 correct repetitions OR 1 correct contextual use). After about 3 failed attempts, increase English guidance and simplify. Increase Spanish only after mastery.
Guided practice: combine phrases and vocab into short requests, then run a short exchange asking for directions to a specific place.
Completion: when all vocab and phrase targets are mastered, say the lesson is complete and ask whether to continue practicing or end the lesson/call.',
  1
),
(
  gen_random_uuid(),
  'Shopping in a Mexican Market',
  'es-MX',
  'en-US',
  'Lesson type: beginner guided lesson.
Lesson objective: asking for prices and buying items at a market.
Vocabulary targets (teach in this order): la camisa (shirt), los zapatos (shoes), la bolsa (bag), el dinero (money), la tarjeta (card).
Phrase targets (teach in this order): "¿Cuánto cuesta?", "Es muy caro", "Me lo llevo".
Progression: start in English. For each target, explain in English, give the Spanish, ask the learner to repeat. Do not move on until mastery (2 correct repetitions OR 1 correct contextual use). After about 3 failed attempts, increase English guidance and simplify. Increase Spanish only after mastery.
Guided practice: combine phrases and vocab into short requests, then run a short haggling or purchasing exchange.
Completion: when all vocab and phrase targets are mastered, say the lesson is complete and ask whether to continue practicing or end the lesson/call.',
  1
);
