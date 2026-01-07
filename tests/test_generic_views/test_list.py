import datetime
import re

import pytest

from django.core.exceptions import ImproperlyConfigured
from django.test.client import AsyncClient
from django.test.utils import CaptureQueriesContext
from django.db import connection
from asgiref.sync import async_to_sync

from django_async_extensions.views.generic.base import AsyncView

from .models import Artist, Author, Book, Page

client = AsyncClient()

@pytest.fixture(autouse=True)
async def url_setting_set(settings):
    old_root_urlconf = settings.ROOT_URLCONF
    settings.ROOT_URLCONF = "test_generic_views.urls"
    yield settings
    settings.ROOT_URLCONF = old_root_urlconf


@pytest.mark.django_db(transaction=True)
class TestListView:
    @pytest.fixture(autouse=True)
    async def setUpTestData(self, async_client: AsyncClient):
        self.artist1 = await Artist.objects.acreate(name="Rene Magritte")
        self.author1 = await Author.objects.acreate(
            name="Roberto Bolaño", slug="roberto-bolano"
        )
        self.author2 = await Author.objects.acreate(
            name="Scott Rosenberg", slug="scott-rosenberg"
        )
        self.book1 = await Book.objects.acreate(
            name="2066", slug="2066", pages=800, pubdate=datetime.date(2008, 10, 1)
        )
        await self.book1.authors.aadd(self.author1)
        self.book2 = await Book.objects.acreate(
            name="Dreaming in Code",
            slug="dreaming-in-code",
            pages=300,
            pubdate=datetime.date(2006, 5, 1),
        )
        self.page1 = await Page.objects.acreate(
            content="I was once bitten by a moose.",
            template="generic_views/page_template.html",
        )

    async def test_items(self):
        res = await client.get("/list/dict/")
        assert res.status_code == 200
        assert res.template_name[0] == "test_generic_views/list.html"
        assert res.context["object_list"][0]["first"] == "John"

    async def test_queryset(self):
        res = await client.get("/list/authors/")
        assert res.status_code == 200
        assert res.template_name[0] == "test_generic_views/author_list.html"
        authors = [author async for author in Author.objects.all()]
        assert list(res.context["object_list"]) == list(authors)
        assert isinstance(res.context["view"], AsyncView)
        assert res.context["author_list"] is res.context["object_list"]
        assert res.context["paginator"] is None
        assert res.context["page_obj"] is None
        assert res.context["is_paginated"] is False

    async def test_paginated_queryset(self):
        await self._make_authors(100)
        res = await client.get("/list/authors/paginated/")
        assert res.status_code == 200
        assert res.template_name[0] == "test_generic_views/author_list.html"
        assert len(res.context["object_list"]) == 30
        assert res.context["author_list"] is res.context["object_list"]
        assert res.context["is_paginated"]
        assert res.context["page_obj"].number == 1
        assert (await res.context["paginator"].anum_pages()) == 4
        assert res.context["author_list"][0].name == "Author 00"
        assert list(res.context["author_list"])[-1].name == "Author 29"

    async def test_paginated_queryset_shortdata(self):
        # Short datasets also result in a paginated view.
        res = await client.get("/list/authors/paginated/")
        assert res.status_code == 200
        assert res.template_name[0] == "test_generic_views/author_list.html"
        authors = [author async for author in Author.objects.all()]
        assert list(res.context["object_list"]) == list(authors)
        assert res.context["author_list"] is res.context["object_list"]
        assert res.context["page_obj"].number == 1
        assert (await res.context["paginator"].anum_pages()) == 1
        assert res.context["is_paginated"] is False

    async def test_paginated_get_page_by_query_string(self):
        await self._make_authors(100)
        res = await client.get("/list/authors/paginated/", {"page": "2"})
        assert res.status_code == 200
        assert res.template_name[0] == "test_generic_views/author_list.html"
        assert len(res.context["object_list"]) == 30
        assert res.context["author_list"] is res.context["object_list"]
        assert res.context["author_list"][0].name == "Author 30"
        assert res.context["page_obj"].number == 2

    async def test_paginated_get_last_page_by_query_string(self):
        await self._make_authors(100)
        res = await client.get("/list/authors/paginated/", {"page": "last"})
        assert res.status_code == 200
        assert len(res.context["object_list"]) == 10
        assert res.context["author_list"] is res.context["object_list"]
        assert res.context["author_list"][0].name == "Author 90"
        assert res.context["page_obj"].number == 4

    async def test_paginated_get_page_by_urlvar(self):
        await self._make_authors(100)
        res = await client.get("/list/authors/paginated/3/")
        assert res.status_code == 200
        assert res.template_name[0] == "test_generic_views/author_list.html"
        assert len(res.context["object_list"]) == 30
        assert res.context["author_list"] is res.context["object_list"]
        assert res.context["author_list"][0].name == "Author 60"
        assert res.context["page_obj"].number == 3

    async def test_paginated_page_out_of_range(self):
        await self._make_authors(100)
        res = await client.get("/list/authors/paginated/42/")
        assert res.status_code == 404

    async def test_paginated_invalid_page(self):
        await self._make_authors(100)
        res = await client.get("/list/authors/paginated/?page=frog")
        assert res.status_code == 404

    async def test_paginated_custom_paginator_class(self):
        await self._make_authors(7)
        res = await client.get("/list/authors/paginated/custom_class/")
        assert res.status_code == 200
        assert (await res.context["paginator"].anum_pages()) == 1
        # Custom pagination allows for 2 orphans on a page size of 5
        assert len(res.context["object_list"]) == 7

    async def test_paginated_custom_page_kwarg(self):
        await self._make_authors(100)
        res = await client.get("/list/authors/paginated/custom_page_kwarg/", {"pagina": "2"})
        assert res.status_code == 200
        assert res.template_name[0] == "test_generic_views/author_list.html"
        assert len(res.context["object_list"]) == 30
        assert res.context["author_list"] is res.context["object_list"]
        assert res.context["author_list"][0].name == "Author 30"
        assert res.context["page_obj"].number == 2

    async def test_paginated_custom_paginator_constructor(self):
        await self._make_authors(7)
        res = await client.get("/list/authors/paginated/custom_constructor/")
        assert res.status_code == 200
        # Custom pagination allows for 2 orphans on a page size of 5
        assert len(res.context["object_list"]) == 7

    async def test_paginated_orphaned_queryset(self):
        await self._make_authors(92)
        res = await client.get("/list/authors/paginated-orphaned/")
        assert res.status_code == 200
        assert res.context["page_obj"].number == 1
        res = await client.get("/list/authors/paginated-orphaned/", {"page": "last"})
        assert res.status_code == 200
        assert res.context["page_obj"].number == 3
        res = await client.get("/list/authors/paginated-orphaned/", {"page": "3"})
        assert res.status_code == 200
        assert res.context["page_obj"].number == 3
        res = await client.get("/list/authors/paginated-orphaned/", {"page": "4"})
        assert res.status_code == 404

    async def test_paginated_non_queryset(self):
        res = await client.get("/list/dict/paginated/")

        assert res.status_code == 200
        assert len(res.context["object_list"]) == 1

    async def test_verbose_name(self):
        res = await client.get("/list/artists/")
        assert res.status_code == 200
        assert res.template_name[0] == "test_generic_views/list.html"
        authors = [artist async for artist in Artist.objects.all()]
        assert list(res.context["object_list"]) == list(authors)
        assert res.context["artist_list"] is res.context["object_list"]
        assert res.context["paginator"] is None
        assert res.context["page_obj"] is None
        assert res.context["is_paginated"] is False

    async def test_allow_empty_false(self):
        res = await client.get("/list/authors/notempty/")
        assert res.status_code == 200
        await Author.objects.all().adelete()
        res = await client.get("/list/authors/notempty/")
        assert res.status_code == 404

    async def test_template_name(self):
        res = await client.get("/list/authors/template_name/")
        assert res.status_code == 200
        authors = [author async for author in Author.objects.all()]
        assert list(res.context["object_list"]) == list(authors)
        assert res.context["author_list"] is res.context["object_list"]
        assert res.template_name[0] == "test_generic_views/list.html"

    async def test_template_name_suffix(self):
        res = await client.get("/list/authors/template_name_suffix/")
        assert res.status_code == 200
        authors = [author async for author in Author.objects.all()]
        assert list(res.context["object_list"]) == list(authors)
        assert res.context["author_list"] is res.context["object_list"]
        assert res.template_name[0] == "test_generic_views/author_objects.html"

    async def test_context_object_name(self):
        res = await client.get("/list/authors/context_object_name/")
        assert res.status_code == 200
        authors = [author async for author in Author.objects.all()]
        assert list(res.context["object_list"]) == list(authors)
        assert "authors" not in res.context
        assert res.context["author_list"] is res.context["object_list"]
        assert res.template_name[0] == "test_generic_views/author_list.html"

    async def test_duplicate_context_object_name(self):
        res = await client.get("/list/authors/dupe_context_object_name/")
        assert res.status_code == 200
        authors = [author async for author in Author.objects.all()]
        assert list(res.context["object_list"]) == list(authors)
        assert "authors" not in res.context
        assert "author_list" not in res.context
        assert res.template_name[0] == "test_generic_views/author_list.html"

    async def test_missing_items(self):
        msg = (
            "AuthorList is missing a QuerySet. Define AuthorList.model, "
            "AuthorList.queryset, or override AuthorList.get_queryset()."
        )
        with pytest.raises(ImproperlyConfigured, match=msg):
            await client.get("/list/authors/invalid/")

    async def test_invalid_get_queryset(self):
        msg = re.escape(
            "AuthorListGetQuerysetReturnsNone requires either a 'template_name' "
            "attribute or a get_queryset() method that returns a QuerySet."
        )
        with pytest.raises(ImproperlyConfigured, match=msg):
            await client.get("/list/authors/get_queryset/")

    def test_paginated_list_view_does_not_load_entire_table(self, client):
        # Regression test for #17535
        async_to_sync(self._make_authors)(3)
        # 1 query for authors
        with CaptureQueriesContext(connection) as ctx:
            client.get("/list/authors/notempty/")
            assert len(ctx.captured_queries) == 1
        
        # same as above + 1 query to test if authors exist + 1 query for pagination
        with CaptureQueriesContext(connection) as ctx:
            client.get("/list/authors/notempty/paginated/")
            assert len(ctx.captured_queries) == 3


    async def test_explicitly_ordered_list_view(self):
        await Book.objects.acreate(
            name="Zebras for Dummies", pages=800, pubdate=datetime.date(2006, 9, 1)
        )
        res = await client.get("/list/books/sorted/")
        assert res.status_code == 200
        assert res.context["object_list"][0].name == "2066"
        assert res.context["object_list"][1].name == "Dreaming in Code"
        assert res.context["object_list"][2].name == "Zebras for Dummies"

        res = await client.get("/list/books/sortedbypagesandnamedec/")
        assert res.status_code == 200
        assert res.context["object_list"][0].name == "Dreaming in Code"
        assert res.context["object_list"][1].name == "Zebras for Dummies"
        assert res.context["object_list"][2].name == "2066"

    @pytest.fixture(autouse=True)
    async def toggle_debug_tests(self, settings):
        settings.DEBUG = True

    async def test_paginated_list_view_returns_useful_message_on_invalid_page(self):
        # test for #19240
        # tests that source exception's message is included in page
        await self._make_authors(1)
        res = await client.get("/list/authors/paginated/2/")
        assert res.status_code == 404
        assert (
            res.context.get("reason")
            == "Invalid page (2): That page contains no results"
        )

    async def _make_authors(self, n):
        await Author.objects.all().adelete()
        for i in range(n):
            await Author.objects.acreate(name="Author %02i" % i, slug="a%s" % i)