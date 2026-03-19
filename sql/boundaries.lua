-- osm2pgsql flex output for administrative boundaries
-- Filters to boundary=administrative with admin_level 2, 4, 6, 8

local admin_boundaries = osm2pgsql.define_table({
    name = 'admin_boundaries',
    ids = { type = 'any', id_column = 'osm_id' },
    columns = {
        { column = 'name',         type = 'text' },
        { column = 'name_en',      type = 'text' },
        { column = 'admin_level',  type = 'integer' },
        { column = 'boundary',     type = 'text' },
        { column = 'type',         type = 'text' },
        { column = 'country_code', type = 'text' },
        { column = 'iso3166_1',    type = 'text' },
        { column = 'iso3166_2',    type = 'text' },
        { column = 'population',   type = 'text' },
        { column = 'wikidata',     type = 'text' },
        { column = 'wikipedia',    type = 'text' },
        { column = 'tags',         type = 'jsonb' },
        { column = 'geom',         type = 'geometry', projection = 4326 },
    },
    indexes = {
        { column = 'osm_id' },
        { column = 'admin_level' },
        { column = 'country_code', where = 'country_code IS NOT NULL' },
        { column = 'name', where = 'name IS NOT NULL' },
        { column = 'geom', method = 'gist' },
    },
})

local allowed_levels = { ['2'] = true, ['4'] = true, ['6'] = true, ['8'] = true }

function osm2pgsql.process_relation(object)
    local tags = object.tags
    if tags.boundary ~= 'administrative' then return end
    if not allowed_levels[tags.admin_level] then return end

    admin_boundaries:insert({
        name         = tags.name,
        name_en      = tags['name:en'],
        admin_level  = tonumber(tags.admin_level),
        boundary     = tags.boundary,
        type         = tags.type,
        country_code = tags.country_code,
        iso3166_1    = tags['ISO3166-1'],
        iso3166_2    = tags['ISO3166-2'],
        population   = tags.population,
        wikidata     = tags.wikidata,
        wikipedia    = tags.wikipedia,
        tags         = tags,
        geom         = object:as_multigeometry(),
    })
end
