-- 🔧 Set the issue you want to export
SET @issue_id = ?ISSUE_ID?;  -- ← change me

SELECT
  /* Title: prefer issue.title; if blank, fall back to first interior story title */
  COALESCE(
    NULLIF(TRIM(i.title), ''),
    ( SELECT NULLIF(TRIM(s.title), '')
      FROM gcd_data.gcd_story s
      WHERE s.issue_id = i.id AND (s.sequence_number IS NULL OR s.sequence_number <> 0)
      ORDER BY s.sequence_number
      LIMIT 1
    )
  ) AS Title,

  sr.name AS Series,
  i.number AS Number,

  ( SELECT COUNT(*)
    FROM gcd_data.gcd_issue i2
    WHERE i2.series_id = i.series_id AND i2.deleted = 0
  ) AS `Count`,

  i.volume AS Volume,

  /* Summary: prefer interior synopsis; if none, fall back (often cover) */
  ( SELECT s.synopsis
    FROM gcd_data.gcd_story s
    WHERE s.issue_id = i.id
    ORDER BY CASE WHEN s.sequence_number = 0 THEN 1 ELSE 0 END, s.sequence_number
    LIMIT 1
  ) AS Summary,

  /* Year / Month from key_date (fallback to on_sale_date) */
  CASE
    WHEN COALESCE(i.key_date, i.on_sale_date) IS NOT NULL
         AND LENGTH(COALESCE(i.key_date, i.on_sale_date)) >= 4
    THEN CAST(SUBSTRING(COALESCE(i.key_date, i.on_sale_date), 1, 4) AS UNSIGNED)
  END AS `Year`,
  CASE
    WHEN COALESCE(i.key_date, i.on_sale_date) IS NOT NULL
         AND LENGTH(COALESCE(i.key_date, i.on_sale_date)) >= 7
    THEN CAST(SUBSTRING(COALESCE(i.key_date, i.on_sale_date), 6, 2) AS UNSIGNED)
  END AS `Month`,

  /* =======================
     Creator credits (INTERIOR via story_credit, with issue_credit fallback)
     ======================= */

  /* Writer / Script / Plot */
  ( SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ', ')
    FROM (
      SELECT TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name)) AS name
      FROM gcd_data.gcd_story s
      JOIN gcd_data.gcd_story_credit sc ON sc.story_id = s.id
      JOIN gcd_data.gcd_credit_type ct   ON ct.id = sc.credit_type_id
      LEFT JOIN gcd_data.gcd_creator c   ON c.id = sc.creator_id
      WHERE s.issue_id = i.id
        AND (s.sequence_number IS NULL OR s.sequence_number <> 0)
        AND (ct.name LIKE 'script%' OR ct.name LIKE 'writer%' OR ct.name LIKE 'plot%')
        AND (sc.deleted = 0 OR sc.deleted IS NULL)
      UNION
      SELECT TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name)) AS name
      FROM gcd_data.gcd_issue_credit ic
      JOIN gcd_data.gcd_credit_type ct ON ct.id = ic.credit_type_id
      LEFT JOIN gcd_data.gcd_creator c ON c.id = ic.creator_id
      WHERE ic.issue_id = i.id
        AND (ct.name LIKE 'script%' OR ct.name LIKE 'writer%' OR ct.name LIKE 'plot%')
        AND (ic.deleted = 0 OR ic.deleted IS NULL)
    ) x
    WHERE NULLIF(name,'') IS NOT NULL
  ) AS Writer,

  /* Penciller */
  ( SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ', ')
    FROM (
      SELECT TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name))
      FROM gcd_data.gcd_story s
      JOIN gcd_data.gcd_story_credit sc ON sc.story_id = s.id
      JOIN gcd_data.gcd_credit_type ct   ON ct.id = sc.credit_type_id
      LEFT JOIN gcd_data.gcd_creator c   ON c.id = sc.creator_id
      WHERE s.issue_id = i.id
        AND (s.sequence_number IS NULL OR s.sequence_number <> 0)
        AND (ct.name LIKE 'pencil%' OR ct.name LIKE 'penc%')
        AND (sc.deleted = 0 OR sc.deleted IS NULL)
      UNION
      SELECT TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name))
      FROM gcd_data.gcd_issue_credit ic
      JOIN gcd_data.gcd_credit_type ct ON ct.id = ic.credit_type_id
      LEFT JOIN gcd_data.gcd_creator c ON c.id = ic.creator_id
      WHERE ic.issue_id = i.id
        AND (ct.name LIKE 'pencil%' OR ct.name LIKE 'penc%')
        AND (ic.deleted = 0 OR ic.deleted IS NULL)
    ) x
    WHERE NULLIF(name,'') IS NOT NULL
  ) AS Penciller,

  /* Inker */
  ( SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ', ')
    FROM (
      SELECT TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name))
      FROM gcd_data.gcd_story s
      JOIN gcd_data.gcd_story_credit sc ON sc.story_id = s.id
      JOIN gcd_data.gcd_credit_type ct   ON ct.id = sc.credit_type_id
      LEFT JOIN gcd_data.gcd_creator c   ON c.id = sc.creator_id
      WHERE s.issue_id = i.id
        AND (s.sequence_number IS NULL OR s.sequence_number <> 0)
        AND (ct.name LIKE 'ink%')
        AND (sc.deleted = 0 OR sc.deleted IS NULL)
      UNION
      SELECT TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name))
      FROM gcd_data.gcd_issue_credit ic
      JOIN gcd_data.gcd_credit_type ct ON ct.id = ic.credit_type_id
      LEFT JOIN gcd_data.gcd_creator c ON c.id = ic.creator_id
      WHERE ic.issue_id = i.id
        AND (ct.name LIKE 'ink%')
        AND (ic.deleted = 0 OR ic.deleted IS NULL)
    ) x
    WHERE NULLIF(name,'') IS NOT NULL
  ) AS Inker,

  /* Colorist (color/colour) */
  ( SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ', ')
    FROM (
      SELECT TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name))
      FROM gcd_data.gcd_story s
      JOIN gcd_data.gcd_story_credit sc ON sc.story_id = s.id
      JOIN gcd_data.gcd_credit_type ct   ON ct.id = sc.credit_type_id
      LEFT JOIN gcd_data.gcd_creator c   ON c.id = sc.creator_id
      WHERE s.issue_id = i.id
        AND (s.sequence_number IS NULL OR s.sequence_number <> 0)
        AND (ct.name LIKE 'color%' OR ct.name LIKE 'colour%')
        AND (sc.deleted = 0 OR sc.deleted IS NULL)
      UNION
      SELECT TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name))
      FROM gcd_data.gcd_issue_credit ic
      JOIN gcd_data.gcd_credit_type ct ON ct.id = ic.credit_type_id
      LEFT JOIN gcd_data.gcd_creator c ON c.id = ic.creator_id
      WHERE ic.issue_id = i.id
        AND (ct.name LIKE 'color%' OR ct.name LIKE 'colour%')
        AND (ic.deleted = 0 OR ic.deleted IS NULL)
    ) x
    WHERE NULLIF(name,'') IS NOT NULL
  ) AS Colorist,

  /* Letterer */
  ( SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ', ')
    FROM (
      SELECT TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name))
      FROM gcd_data.gcd_story s
      JOIN gcd_data.gcd_story_credit sc ON sc.story_id = s.id
      JOIN gcd_data.gcd_credit_type ct   ON ct.id = sc.credit_type_id
      LEFT JOIN gcd_data.gcd_creator c   ON c.id = sc.creator_id
      WHERE s.issue_id = i.id
        AND (s.sequence_number IS NULL OR s.sequence_number <> 0)
        AND (ct.name LIKE 'letter%')
        AND (sc.deleted = 0 OR sc.deleted IS NULL)
      UNION
      SELECT TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name))
      FROM gcd_data.gcd_issue_credit ic
      JOIN gcd_data.gcd_credit_type ct ON ct.id = ic.credit_type_id
      LEFT JOIN gcd_data.gcd_creator c ON c.id = ic.creator_id
      WHERE ic.issue_id = i.id
        AND (ct.name LIKE 'letter%')
        AND (ic.deleted = 0 OR ic.deleted IS NULL)
    ) x
    WHERE NULLIF(name,'') IS NOT NULL
  ) AS Letterer,

  /* =======================
     Cover artist (cover story OR cover-type credits)
     ======================= */
  ( SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ', ')
    FROM (
      /* cover story-level pencils/inks/art/cover credits */
      SELECT TRIM(COALESCE(NULLIF(sc.credited_as,''), NULLIF(sc.credit_name,''), c.gcd_official_name)) AS name
      FROM gcd_data.gcd_story s
      JOIN gcd_data.gcd_story_credit sc ON sc.story_id = s.id
      JOIN gcd_data.gcd_credit_type ct   ON ct.id = sc.credit_type_id
      LEFT JOIN gcd_data.gcd_creator c   ON c.id = sc.creator_id
      WHERE s.issue_id = i.id
        AND (s.sequence_number = 0 OR ct.name LIKE 'cover%')
        AND (ct.name LIKE 'pencil%' OR ct.name LIKE 'penc%' OR ct.name LIKE 'ink%' OR ct.name LIKE 'art%' OR ct.name LIKE 'cover%')
        AND (sc.deleted = 0 OR sc.deleted IS NULL)
      UNION
      /* issue-level explicit cover credits */
      SELECT TRIM(COALESCE(NULLIF(ic.credited_as,''), NULLIF(ic.credit_name,''), c.gcd_official_name)) AS name
      FROM gcd_data.gcd_issue_credit ic
      JOIN gcd_data.gcd_credit_type ct ON ct.id = ic.credit_type_id
      LEFT JOIN gcd_data.gcd_creator c ON c.id = ic.creator_id
      WHERE ic.issue_id = i.id
        AND (ct.name LIKE 'cover%')
        AND (ic.deleted = 0 OR ic.deleted IS NULL)
    ) z
    WHERE NULLIF(name,'') IS NOT NULL
  ) AS CoverArtist,

  /* Publisher: indicia if present; else series publisher */
  COALESCE(ip.name, p.name) AS Publisher,

  /* Genre from story text; normalize semicolons to commas */
  ( SELECT TRIM(BOTH ', ' FROM
           REPLACE(
             GROUP_CONCAT(DISTINCT NULLIF(TRIM(s.genre), '') SEPARATOR ', '),
             ';', ','
           ))
    FROM gcd_data.gcd_story s
    WHERE s.issue_id = i.id
  ) AS Genre,

  /* Characters: normalized link if present, else free-text from story */
  COALESCE(
    ( SELECT NULLIF(GROUP_CONCAT(DISTINCT c2.name SEPARATOR ', '), '')
      FROM gcd_data.gcd_story s
      LEFT JOIN gcd_data.gcd_story_character sc2 ON sc2.story_id = s.id
      LEFT JOIN gcd_data.gcd_character c2        ON c2.id = sc2.character_id
      WHERE s.issue_id = i.id
    ),
    ( SELECT TRIM(BOTH ', ' FROM
             REPLACE(
               GROUP_CONCAT(DISTINCT NULLIF(TRIM(s.characters), '') SEPARATOR ', '),
               ';', ','
             ))
      FROM gcd_data.gcd_story s
      WHERE s.issue_id = i.id )
  ) AS Characters,

  i.rating AS AgeRating,
  'en'     AS LanguageISO,   -- change default if you wish
  'No'     AS Manga

FROM gcd_data.gcd_issue i
JOIN gcd_data.gcd_series sr                 ON sr.id = i.series_id
LEFT JOIN gcd_data.gcd_publisher p          ON p.id = sr.publisher_id
LEFT JOIN gcd_data.gcd_indicia_publisher ip ON ip.id = i.indicia_publisher_id
WHERE i.id = @issue_id
LIMIT 1;
