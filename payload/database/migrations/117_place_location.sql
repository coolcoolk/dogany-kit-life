-- Separate pasted calendar location labels from addresses, retaining rooms on events.
-- reversible: run tools/rollback-place-location-117.sql (before subsequent edits); it restores the journaled rows and sets PRAGMA user_version=116.
-- Every modified registry, label, address, block and travel-rule row is journaled.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_117(v);
CREATE TEMP TRIGGER _guard_117_refuse BEFORE INSERT ON _guard_117 BEGIN
 SELECT RAISE(FAIL, 'migration 117 requires user_version 116');
END;
INSERT INTO _guard_117 SELECT 1 WHERE (SELECT user_version FROM pragma_user_version)<>116;
BEGIN IMMEDIATE;
CREATE TABLE place_location_117_log (
 table_name TEXT NOT NULL, row_key TEXT NOT NULL,
 before_json TEXT, after_json TEXT,
 PRIMARY KEY(table_name,row_key)
);
-- Find an address token at a whitespace boundary, never inside a venue word.
CREATE TEMP TABLE _place117 AS
WITH RECURSIVE prefixes(prefix) AS (
 VALUES('대한민국'),('서울'),('부산'),('대구'),('인천'),('광주'),('대전'),('울산'),
 ('세종'),('경기'),('강원'),('충청'),('충북'),('충남'),('전라'),('전북'),('전남'),
 ('경상'),('경북'),('경남'),('제주'),
 ('서울특별시'),('부산광역시'),('대구광역시'),('인천광역시'),('광주광역시'),
 ('대전광역시'),('울산광역시'),('세종특별자치시'),('경기도'),('강원도'),
 ('강원특별자치도'),('충청북도'),('충청남도'),('전라북도'),('전라남도'),
 ('전북특별자치도'),('경상북도'),('경상남도'),('제주도'),('제주특별자치도')
), chars(id,name,pos) AS (
 SELECT id,name,1 FROM place WHERE status='active'
 UNION ALL SELECT id,name,pos+1 FROM chars WHERE pos<length(name)
), starts AS (
 SELECT id,name,min(pos) AS pos FROM chars
 WHERE substr(name,pos,1)=' ' AND EXISTS (
  SELECT 1 FROM prefixes WHERE substr(name,pos+1) LIKE prefix||' %'
 ) GROUP BY id
), parts AS (
 SELECT id,trim(substr(name,1,pos-1),' ,') AS label,
        trim(substr(name,pos+1)) AS address FROM starts
)
SELECT id,CASE WHEN instr(label,' · ')>0 AND (
                  substr(label,instr(label,' · ')+3) GLOB '[A-Za-z][0-9]*'
                  OR substr(label,instr(label,' · ')+3) GLOB '[0-9]*'
                  OR substr(label,instr(label,' · ')+3) LIKE '%룸'
                  OR substr(label,instr(label,' · ')+3) GLOB '[A-Za-z]실')
              THEN trim(substr(label,1,instr(label,' · ')-1))
               ELSE label END AS name,address,CAST(NULL AS INTEGER) AS target_id
FROM parts WHERE label<>'' AND address GLOB '*[0-9]*';
-- Calendar exports can prefix the address with a postcode after the label.
UPDATE _place117 SET name=rtrim(substr(name,1,length(name)-5),' ,')
WHERE substr(name,-5) GLOB '[0-9][0-9][0-9][0-9][0-9]'
 AND substr(name,-6,1) IN (' ', ',');
-- Only an unambiguous, already clean address owner may absorb a duplicate.
UPDATE _place117 SET target_id=(
 SELECT min(p.id) FROM place p
 WHERE p.status='active' AND p.id NOT IN (SELECT id FROM _place117)
 AND EXISTS (
  SELECT 1 FROM place_address a WHERE a.place_id=p.id AND a.kind!='map_url'
  AND replace(replace(trim(a.value),'대한민국 ',''),' ','')=
      replace(replace(_place117.address,'대한민국 ',''),' ','')
 ) HAVING count(*)=1
);
INSERT INTO place_location_117_log
SELECT 'place',cast(p.id AS TEXT),
 json_object('name',p.name,'display_name',p.display_name,'status',p.status,'address',p.address),NULL
FROM place p JOIN _place117 x ON x.id=p.id;
INSERT INTO place_location_117_log
SELECT 'label',json_array(l.namespace,l.token,l.lang),json_object('display',l.display),NULL
FROM label l JOIN place p ON l.namespace='place' AND l.token=p.token
JOIN _place117 x ON x.id=p.id WHERE l.display=p.name;
INSERT INTO place_location_117_log
SELECT 'block',cast(b.id AS TEXT),json_object('place_id',b.place_id),NULL
FROM block b JOIN _place117 x ON x.id=b.place_id WHERE x.target_id IS NOT NULL;
INSERT INTO place_location_117_log
SELECT 'travel_rule',cast(r.id AS TEXT),json_object('place_id',r.place_id),NULL
FROM travel_rule r JOIN _place117 x ON x.id=r.place_id WHERE x.target_id IS NOT NULL;
-- Address inserts are logged after the mirror triggers run, with before=NULL.
CREATE TEMP TABLE _address117_before AS SELECT id FROM place_address;
INSERT INTO place_address(place_id,kind,value,is_primary)
SELECT x.id,'legacy',x.address,
 NOT EXISTS(SELECT 1 FROM place_address a WHERE a.place_id=x.id AND a.is_primary=1)
FROM _place117 x WHERE NOT EXISTS (
 SELECT 1 FROM place_address a WHERE a.place_id=x.id AND a.value=x.address
);
UPDATE label SET display=(SELECT x.name FROM _place117 x JOIN place p ON p.id=x.id
 WHERE p.token=label.token)
WHERE namespace='place' AND EXISTS (
 SELECT 1 FROM _place117 x JOIN place p ON p.id=x.id
 WHERE p.token=label.token AND label.display=p.name
);
UPDATE place SET name=(SELECT name FROM _place117 WHERE id=place.id),
 display_name=CASE WHEN display_name=name THEN (SELECT name FROM _place117 WHERE id=place.id)
                   ELSE display_name END,
 status=CASE WHEN (SELECT target_id FROM _place117 WHERE id=place.id) IS NOT NULL
             THEN 'gone' ELSE status END
WHERE id IN (SELECT id FROM _place117);
UPDATE block SET place_id=(SELECT target_id FROM _place117 WHERE id=block.place_id)
WHERE place_id IN (SELECT id FROM _place117 WHERE target_id IS NOT NULL);
UPDATE travel_rule SET place_id=(SELECT target_id FROM _place117 WHERE id=travel_rule.place_id)
WHERE place_id IN (SELECT id FROM _place117 WHERE target_id IS NOT NULL);
UPDATE place_location_117_log SET after_json=(
 SELECT json_object('name',p.name,'display_name',p.display_name,'status',p.status,'address',p.address)
 FROM place p WHERE p.id=cast(row_key AS INTEGER)) WHERE table_name='place';
UPDATE place_location_117_log SET after_json=(
 SELECT json_object('display',l.display) FROM label l
 WHERE l.namespace=json_extract(row_key,'$[0]') AND l.token=json_extract(row_key,'$[1]')
 AND l.lang=json_extract(row_key,'$[2]')) WHERE table_name='label';
UPDATE place_location_117_log SET after_json=(
 SELECT json_object('place_id',b.place_id) FROM block b WHERE b.id=cast(row_key AS INTEGER))
 WHERE table_name='block';
UPDATE place_location_117_log SET after_json=(
 SELECT json_object('place_id',r.place_id) FROM travel_rule r WHERE r.id=cast(row_key AS INTEGER))
 WHERE table_name='travel_rule';
INSERT INTO place_location_117_log
SELECT 'place_address',cast(a.id AS TEXT),NULL,
 json_object('place_id',a.place_id,'kind',a.kind,'value',a.value,'is_primary',a.is_primary)
FROM place_address a WHERE a.id NOT IN (SELECT id FROM _address117_before);
DROP TABLE _address117_before;
DROP TABLE _place117;
PRAGMA user_version=117;
COMMIT;
DROP TABLE _guard_117;
