changes made to current db to include password for unlocking lockers.

please update database with following instructions:

1. ensure docker compose is running:
   docker-compose up -d

2. open postgre shell:
   docker exec -it smart-locker-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

3. run:
   ALTER TABLE locker_reservations
   ADD COLUMN IF NOT EXISTS unlock_password_hash TEXT NULL;
   ALTER TABLE locker_reservations
   DROP CONSTRAINT IF EXISTS locker_reservations_compartment_id_key;

note:

- the updates are required to save password for unlocking the locker.
- alter table required to ensure that the same locker can be reused again after parcel is collected.

4. exit postgre sql:
   \q

5. start the application:
   docker-compose up -d --build

testing the parcel receieve feature:

1. ensure that the frontend build is up to date:
   docker-compose up -d --build

2. open the application on browser:
   http://localhost:3000

3. create a new booking from the application UI and submit.

4. in vsc terminal, find booking created and copy the id:
   Invoke-RestMethod -UseBasicParsing http://localhost:3000/api/reservations

5. run the following command. replace YOUR_RESERVATION_ID with the id previously copied:
   $body = @{} | ConvertTo-Json

Invoke-RestMethod `  -Uri "http://localhost:3000/api/reservations/YOUR_RESERVATION_ID/arrive"`
-Method POST `  -ContentType "application/json"`
-Body $body

6. copy the collection_password from the output.

7. run the following command to ensure the parcel is ready for collection:
   Invoke-RestMethod http://localhost:3000/api/parcels/collection

8. return to http://localhost:3000 and click on receive parcel.

9. select parcel from the list and paste in password previously copied.

10. if successful, the system should return a message that the locker is successfully unlocked.

11. note that when clicking 'receive parcel' again, the collected parcel is not longer in the list.
