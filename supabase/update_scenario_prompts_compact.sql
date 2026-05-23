-- Compact scenario prompts (structured lists, same instructional meaning)

UPDATE scenarios
SET system_prompt = $$
[SCENARIO]
Lesson type: beginner guided lesson.
Objective: asking for directions and finding places.
Vocab (order): el bano (bathroom), la calle (street), el museo (museum), el hotel (hotel), el tren (train).
Phrases (order): "Donde esta ...?", "A la derecha", "A la izquierda".
Guided practice: combine phrases and vocab into short requests, then run a short exchange asking for directions to a specific place.
$$
WHERE id = '2b8edde7-7344-4599-8301-e4b77d6a4b26';

UPDATE scenarios
SET system_prompt = $$
[SCENARIO]
Lesson type: beginner guided lesson.
Objective: asking for prices and buying items at a market.
Vocab (order): la camisa (shirt), los zapatos (shoes), la bolsa (bag), el dinero (money), la tarjeta (card).
Phrases (order): "Cuanto cuesta?", "Es muy caro", "Me lo llevo".
Guided practice: combine phrases and vocab into short requests, then run a short haggling or purchasing exchange.
$$
WHERE id = '59c120f1-58c2-40da-8429-eb093aeb4145';

UPDATE scenarios
SET system_prompt = $$
[SCENARIO]
Lesson type: beginner guided lesson.
Objective: ordering food and drinks at a restaurant.
Vocab (order): el menu (menu), el agua (water), el cafe (coffee), la cuenta (bill), el pan (bread).
Phrases (order): "Quiero ...", "Me trae ...?", "La cuenta, por favor".
Guided practice: combine phrases and vocab into short requests, then run a short ordering exchange after all items.
$$
WHERE id = 'f5608d38-c2ee-4cc3-b9f4-33be4838acf7';
