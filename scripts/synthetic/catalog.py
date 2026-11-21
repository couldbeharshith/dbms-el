"""Topic catalog shared by the synthetic-data generator and the usage simulator.

nature is the default behaviour of the topic in a simulated life:
  interest = recurs over weeks, trivia = one-off lookup, errand = rare task, routine = daily habit
"""

CATALOG: list[tuple[str, str, str]] = [
    # (id, nature, description)
    ("apple_products", "interest", "researching Apple products: iPhone, MacBook, iPad, AirPods, Apple Watch, prices and comparisons"),
    ("android_phones", "interest", "comparing Android phones: Pixel, Samsung Galaxy, OnePlus, specs, camera reviews"),
    ("gaming_laptops", "interest", "shopping for a gaming laptop: RTX GPUs, refresh rates, Asus ROG, Lenovo Legion, HP Victus"),
    ("pc_building", "interest", "building a desktop PC: CPU and GPU benchmarks, PCPartPicker lists, cabinets, PSU choice"),
    ("mechanical_keyboards", "interest", "mechanical keyboards: switches, keycaps, Keychron, hot-swap boards, group buys"),
    ("headphones_audio", "interest", "buying headphones and earbuds: Sony, Bose, Sennheiser, noise cancelling reviews"),
    ("photography_gear", "interest", "photography: mirrorless cameras, lenses, Sony Alpha, Canon, editing in Lightroom"),
    ("running_fitness", "interest", "running and gym fitness: workout plans, running shoes, protein, strength training logs"),
    ("dbms_study", "interest", "database course study: normalization, BCNF, SQL joins, transactions, indexing, ER diagrams"),
    ("ml_study", "interest", "machine learning coursework: scikit-learn, neural networks, gradient descent, Kaggle notebooks"),
    ("dsa_practice", "interest", "coding interview practice: LeetCode problems, graphs, dynamic programming, time complexity"),
    ("web_dev_react", "interest", "web development: React hooks, TypeScript, Tailwind, Next.js docs, Vercel deploys"),
    ("linux_homelab", "interest", "home lab tinkering: Raspberry Pi, Docker compose, Pi-hole, self-hosting, Proxmox"),
    ("guitar_learning", "interest", "learning guitar: chord charts, fingerstyle tutorials, tabs, buying an acoustic guitar"),
    ("indian_cooking", "interest", "cooking Indian food: recipes for biryani, dal, paneer, meal prep, spice guides"),
    ("stock_investing", "interest", "investing: mutual funds, SIPs, Nifty charts, stock screeners, Zerodha, quarterly results"),
    ("cricket_news", "interest", "cricket: IPL scores, match highlights, player stats, India fixtures"),
    ("football_epl", "interest", "football: Premier League tables, transfer rumours, match reports, fantasy EPL"),
    ("anime_watching", "interest", "anime: episode streaming, MyAnimeList, seasonal charts, manga chapters"),
    ("sci_fi_books", "interest", "reading science fiction books: Goodreads lists, Kindle deals, reviews of Dune and Asimov"),
    ("japanese_learning", "interest", "learning Japanese: hiragana and kanji drills, Duolingo, JLPT N5 resources, anki decks"),
    ("electric_vehicles", "interest", "electric vehicles: Tata Nexon EV, Ather scooters, range tests, charging networks, subsidies"),
    ("job_search", "interest", "job and internship search: LinkedIn postings, resume templates, interview experiences, referrals"),
    ("goa_trip_planning", "interest", "planning a Goa trip: flights, beach resorts, scooter rentals, itineraries, budgets"),
    ("sleep_meditation", "interest", "sleep and meditation: guided meditation apps, sleep hygiene articles, breathing exercises"),
    ("trivia_geography", "trivia", "one-off geography lookups: capital of France, longest river, population of a country"),
    ("trivia_history", "trivia", "one-off history lookups: when did World War 2 end, who built the Taj Mahal"),
    ("trivia_science", "trivia", "one-off science questions: why is the sky blue, how far is the moon, boiling point of water"),
    ("unit_conversion", "trivia", "one-off conversions: cups to ml, USD to INR today, miles to km, Celsius to Fahrenheit"),
    ("word_lookup", "trivia", "one-off dictionary lookups: meaning of a word, synonyms, spelling, translation of a phrase"),
    ("celebrity_trivia", "trivia", "one-off celebrity lookups: age of an actor, who directed a movie, a singer's net worth"),
    ("weather_check", "trivia", "quick weather checks: today's forecast, will it rain tomorrow, AQI of the city"),
    ("electricity_bill", "errand", "paying the electricity bill or a recharge on a payments app, receipts and due dates"),
    ("movie_tickets", "errand", "booking movie tickets: BookMyShow showtimes, seat selection, offers"),
    ("food_delivery", "errand", "ordering food online: Swiggy and Zomato menus, offers, tracking an order"),
    ("flight_checkin", "errand", "airline web check-in, boarding pass, baggage rules for a single trip"),
    ("package_tracking", "errand", "tracking a parcel from Amazon or Flipkart, delivery updates, return requests"),
    ("email_inbox", "routine", "daily email checking: Gmail inbox, newsletters, calendar invites, replying to threads"),
    ("morning_news", "routine", "reading the morning news: headlines, The Hindu, Hacker News, tech and business news"),
    ("youtube_evening", "routine", "evening YouTube watching: tech reviews, vlogs, lo-fi streams, recommended videos"),
    ("social_media", "routine", "scrolling social media: Instagram feed, Reddit threads, X timeline, WhatsApp web"),
]

TOPIC_IDS = [t[0] for t in CATALOG]


def catalog_lines(ids: list[str] | None = None) -> str:
    wanted = set(ids) if ids else None
    return "\n".join(f"- {i}: {d}" for i, _, d in CATALOG if wanted is None or i in wanted)
