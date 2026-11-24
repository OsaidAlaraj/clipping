-- Owner notes 2, note 2: the ad (paid-partnership) label can be switched off per campaign. On by default, so nothing changes for
-- existing campaigns. A brief that requires the label keeps it on whatever this column says (ad_label.py, a hard rule).
alter table campaigns add column ad_label boolean not null default true;
