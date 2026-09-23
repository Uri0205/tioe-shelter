# TIOE Shelter Infrastructure Portfolio v0.6

גרסת Portfolio מעודכנת למודול הסככות.

## עקרונות המוצר ב-v0.6

המסך בנוי סביב שלושה מדדי ליבה:

1. **מצב נוכחי** — עליות ביום בתחנות ללא סככה.
2. **פוטנציאל אופטימיזציה** — תוספת כיסוי נטו מהקצאה מחדש של סככות קיימות, על בסיס סט one-to-one.
3. **השפעה שמומשה** — שינוי כיסוי המזוהה עם שינוי בסיווג התשתית בין שני snapshots. אין ייחוס סיבתי אוטומטי לשינויי ביקוש או שירות.

## דיוק טרמינולוגי

- המוצר משתמש במונח **"עליות"** ולא "נוסעים", כי `OnDay` אינו מזהה אנשים ייחודיים.
- תחנה שסוג התשתית שלה לא ידוע **אינה** מסווגת כתחנה ללא סככה.
- מוצג כיסוי הנתונים של `shed_structure` בכל עיר.
- `OnDay` מוצג כממוצע עליות/תיקופים ביום חול בהתאם למטא-דאטה של המקור.

## תוספת כיסוי נטו

לכל העברה מוצעת:

`Net Coverage Gain = Recipient OnDay - Donor OnDay`

הסכימה העירונית נעשית רק על סט הקצאה one-to-one, כך שאותה סככה לא נספרת יותר מפעם אחת.

## מעקב שינוי

כאשר קיימים לפחות שני snapshots, המערכת מציגה:

- שינוי כולל בעליות בתחנות ללא סככה.
- שינוי כיסוי המזוהה עם מעבר `POLE → SHELTER` או `SHELTER → POLE`.
- יתר השינוי — מוצג בנפרד ואינו מיוחס אוטומטית לביקוש, שירות או סיבה אחרת.

## הפעלה מקומית

```bash
pip install -r requirements.txt
streamlit run app.py
```

`Stations.xlsx` צריך להיות תחת `data/Stations.xlsx`, או להגדיר את משתנה הסביבה `TIOE_STATIONS_PATH`.

## Provenance

- `shed_structure`: REPORTED
- `OnDay`: REPORTED
- city aggregates / relocation gain / trend metrics: CALCULATED
- physical relocation feasibility: UNAVAILABLE
- decision tier: REVIEW
