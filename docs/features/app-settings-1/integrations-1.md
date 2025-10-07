---
description: Setup your local MySQL Database
icon: square-sliders
---

# MySQL Database Setup

Once you have your MySQL database dump - the file should be **`current.zip`** - you're ready to setup your local database.

### Steps to Install MySQL and Import Your Data

1. Extract the contents of **`current.zip`** to a location available to your Docker or Portainer setup
   1. For example, persistent local storage for Docker is at `\.docker` so I'll create `\.docker\clu\gcd-data\current.sql`
2. Rename the extracted file to `current.sql`&#x20;
   1. the date formatted naming should import, but this makes it consistent for all users
3. Create a new Docker container using the below `docker-compose` settings

```dockercompose
  version: "3.8"

  services:
    mysql:
      image: mysql:9.0
      container_name: mysql-gcd
      networks:
        - gcd-network
      environment:
        MYSQL_ROOT_PASSWORD: strong-root-password
        MYSQL_DATABASE: gcd_data
        MYSQL_USER: clu
        MYSQL_PASSWORD: strong-user-password
      volumes:
        - mysql_data:/var/lib/mysql
        - \YOU-UNZIPPED-MYSQL-FILE-LOCATION\current.sql:/docker-entrypoint-initdb.d/01-current.sql:ro
        - \YOU-UNZIPPED-MYSQL-FILE-LOCATION:/app/gcd-data:ro
      ports:
        - "3306:3306"

  volumes:
    mysql_data:

  networks:
    gcd-network:
      external: true
```

4. When you start the container for the first time - MySQL v9 will be installed, the database will be created and the data from `current.sql` will be imported. Length of the import will be dependent on the download size and the speed of your machine.\
   \
   &#xNAN;_&#x49;nitial import should take around 60-minutes._

Once the DB is setup, you'll update your CLU docker-compose to use the same network and connect to the local GCD database.
