Let's add support for another metadata source.

We will use the Metron API to fetch metadata for comics by installing the Mokkari package at https://mokkari.readthedocs.io/en/latest/

To support this, we will need to add fields for Metron username and password on config.html (on the downloads and API tab), config.py, and config.ini 

All code should be add as a model in /models/metron.py and we will import into app.py as needed

ComicVine is still the most widely used metadata source, but Metron has more details for recently released comics.

I want the Metron class to have methods to fetch metadata for a single comic and each function should be separate, allowing us to combine and call them as needed. Build the model, class and base functions and then I will note in later steps where to call them based on other criteria 

1. Using the cvinfo file in the directory, we are going to look for the metron_series_id. 

    a. metron_series_id: 10354
    
2. If not found, we can use the comicvine series id to get the metron_series_id

    a. cvinfo will have https://comicvine.gamespot.com/absolute-flash/4050-162847/
    b. We will use the 162847 to get the metron_series_id
    c. Add the metron_series_id to the cvinfo file

3. Use the metron_series_id to get the issue metadata
4. map the issue metadata from Metron to the ComicInfo.xml file 
5. Use this data mapping to pass the data 'generate_comicinfo_xml' function in app.py

Here's example code that shows how to use the Metron API to get the series metadata:

```python
import mokkari

# Initialize the API
# Replace 'username' and 'password' with your Metron credentials
api = mokkari.api('username', 'password')

series_id = 10354
issue_num = "10"

# 1. Search for the issue within the series
params = {
    "series_id": series_id,
    "number": issue_num
}

issues = api.issues_list(params)

if issues:
    # 2. Get the specific Issue ID from the search results
    metron_issue_id = issues[0].id
    
    # 3. Fetch the full detailed metadata for that issue
    details = api.issue(metron_issue_id)
    
else:
    app_logger.error("Issue not found.")
```

Important Notes for Metron/Mokkari
Data Types: The number parameter is often treated as a string by the API to accommodate numbers like "10.1" or "Annual 1".

The "Double Fetch": In mokkari, the issues_list returns a summary object (IssueListSchema). To get the full details (like credits, characters, and descriptions), it is best practice to take the id from the list search and then call api.issue(id) to get the complete IssueSchema object.

Full JSON returned for the example issue

```json
{
    "id": 157217,
    "publisher": {
        "id": 2,
        "name": "DC Comics"
    },
    "imprint": null,
    "series": {
        "id": 10354,
        "name": "Absolute Flash",
        "sort_name": "Absolute Flash",
        "volume": 1,
        "year_began": 2025,
        "series_type": {
            "id": 13,
            "name": "Single Issue"
        },
        "genres": [
            {
                "id": 10,
                "name": "Super-Hero"
            }
        ]
    },
    "number": "10",
    "alt_number": "",
    "title": "",
    "name": [
        "Rogues' Revenge, Part Two"
    ],
    "cover_date": "2026-02-01",
    "store_date": "2025-12-17",
    "foc_date": "2025-11-17",
    "price": "4.99",
    "price_currency": "USD",
    "rating": {
        "id": 4,
        "name": "Teen Plus"
    },
    "sku": "1025DC0068",
    "isbn": "",
    "upc": "76194138792501011",
    "page": 32,
    "desc": "THE ROGUES AND ABSOLUTE FLASH SEEK REVENGE! The deeper into Fort Fox Wally West goes, the more mysteries appear for him to chase. And whose voice is that calling to him?",
    "image": "https://static.metron.cloud/media/issue/2025/11/16/fef6d4500225477eb3ea7edc5af73ab8.jpg",
    "cover_hash": "c8d1a2c4db302f7b",
    "arcs": [],
    "credits": [
        {
            "id": 345,
            "creator": "Adriano Lucas",
            "role": [
                {
                    "id": 5,
                    "name": "Colorist"
                }
            ]
        },
        {
            "id": 69,
            "creator": "Andrew Marino",
            "role": [
                {
                    "id": 32,
                    "name": "Senior Editor"
                }
            ]
        },
        {
            "id": 11592,
            "creator": "Ash Padilla",
            "role": [
                {
                    "id": 12,
                    "name": "Assistant Editor"
                }
            ]
        },
        {
            "id": 346,
            "creator": "Clayton Crain",
            "role": [
                {
                    "id": 7,
                    "name": "Cover"
                }
            ]
        },
        {
            "id": 6499,
            "creator": "Haining",
            "role": [
                {
                    "id": 7,
                    "name": "Cover"
                }
            ]
        },
        {
            "id": 53,
            "creator": "Jeff Lemire",
            "role": [
                {
                    "id": 1,
                    "name": "Writer"
                }
            ]
        },
        {
            "id": 172,
            "creator": "Jim Lee",
            "role": [
                {
                    "id": 19,
                    "name": "President"
                },
                {
                    "id": 31,
                    "name": "Publisher"
                },
                {
                    "id": 18,
                    "name": "Chief Creative Officer"
                }
            ]
        },
        {
            "id": 1556,
            "creator": "Joe Quinones",
            "role": [
                {
                    "id": 7,
                    "name": "Cover"
                }
            ]
        },
        {
            "id": 201,
            "creator": "Katie Kubert",
            "role": [
                {
                    "id": 11,
                    "name": "Group Editor"
                }
            ]
        },
        {
            "id": 59,
            "creator": "Marie Javins",
            "role": [
                {
                    "id": 20,
                    "name": "Editor In Chief"
                }
            ]
        },
        {
            "id": 2027,
            "creator": "Nick Robles",
            "role": [
                {
                    "id": 2,
                    "name": "Artist"
                },
                {
                    "id": 7,
                    "name": "Cover"
                }
            ]
        },
        {
            "id": 62,
            "creator": "Tom Napolitano",
            "role": [
                {
                    "id": 6,
                    "name": "Letterer"
                }
            ]
        }
    ],
    "characters": [
        {
            "id": 333,
            "name": "Captain Boomerang",
            "modified": "2025-02-18T15:38:52.297381-05:00"
        },
        {
            "id": 334,
            "name": "Captain Cold",
            "modified": "2025-11-13T13:17:05.207225-05:00"
        },
        {
            "id": 38028,
            "name": "Elenore Thawne",
            "modified": "2025-07-16T09:17:15.687329-04:00"
        },
        {
            "id": 400,
            "name": "Golden Glider",
            "modified": "2025-02-18T15:51:45.333342-05:00"
        },
        {
            "id": 37629,
            "name": "Grodd",
            "modified": "2025-06-18T07:04:39.744492-04:00"
        },
        {
            "id": 335,
            "name": "Heat Wave",
            "modified": "2025-02-18T15:51:48.206329-05:00"
        },
        {
            "id": 40466,
            "name": "Rudy West (Earth Alpha)",
            "modified": "2025-10-15T14:14:52.289727-04:00"
        },
        {
            "id": 1352,
            "name": "Trickster (Jesse)",
            "modified": "2025-02-18T15:59:57.370749-05:00"
        },
        {
            "id": 39574,
            "name": "Wally West (Earth Alpha)",
            "modified": "2025-09-17T10:12:01.467600-04:00"
        }
    ],
    "teams": [
        {
            "id": 36,
            "name": "Rogues",
            "modified": "2025-02-19T01:11:47.094294-05:00"
        }
    ],
    "universes": [
        {
            "id": 157,
            "name": "Absolute Universe",
            "modified": "2025-04-16T09:21:47.838281-04:00"
        }
    ],
    "reprints": [],
    "variants": [
        {
            "name": "Cover B Haining Variant",
            "price": "5.99",
            "sku": "1025DC0069",
            "upc": "76194138792501021",
            "image": "https://static.metron.cloud/media/variants/2025/11/16/a5d24975ef0c44a0b54ff25737c58a63.jpg"
        },
        {
            "name": "Cover C Clayton Crain Variant",
            "price": "5.99",
            "sku": "1025DC0070",
            "upc": "76194138792501031",
            "image": "https://static.metron.cloud/media/variants/2025/11/16/4eece46fb5c947cca03f1f50fd6ceb22.jpg"
        },
        {
            "name": "Cover D Joe Quinones Variant",
            "price": "5.99",
            "sku": "1025DC0071",
            "upc": "76194138792501041",
            "image": "https://static.metron.cloud/media/variants/2025/11/16/55d0073027ae4e98bb6549c6b1e063e3.jpg"
        }
    ],
    "cv_id": 1148693,
    "gcd_id": 2795874,
    "resource_url": "https://metron.cloud/issue/absolute-flash-2025-10/",
    "modified": "2025-12-17T09:35:05.450300-05:00"
}
```

Field Mapping for ComicInfo.xml

```xml
<?xml version="1.0"?>
<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Title>name</Title>
  <Series>series.name</Series>
  <Number>number</Number>
  <Count>12</Count>
  <Volume>series.volume</Volume>
  <Summary>desc</Summary>
  <Year>cover_date</Year>
  <Month>cover_date</Month>
  <Writer>credits.role.name=Writer credits.creator</Writer>
  <Penciller>credits.role.name=Penciller credits.creator</Penciller>
  <Inker>credits.role.name=Inker credits.creator</Inker>
  <Colorist>credits.role.name=Colorist credits.creator</Colorist>
  <Letterer>credits.role.name=Letterer credits.creator</Letterer>
  <CoverArtist>credits.role.name=Cover credits.creator</CoverArtist>
  <Publisher>publisher.name</Publisher>
  <Genre>series.genre.name</Genre>
  <Characters>data from characters array</Characters>
  <AgeRating>rating.name</AgeRating>
  <LanguageISO>en</LanguageISO>
  <Manga>No</Manga>
</ComicInfo>
```

Notes data would be populated with:

notes = f"Metadata from Metron. Resource URL: {issue_data.get('resource_url', 'Unknown')} — modified {issue_data.get('modified', 'Unknown')}."