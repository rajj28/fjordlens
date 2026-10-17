"""Bounded discovery regressions: newly found articles still need their own dated evidence."""
import unittest

from fjordlens import discovery, gate
from fjordlens.articles import article_published_date, extract_dated_links
from fjordlens.core import Profile, validate
from fjordlens.html import parse_html
from fjordlens.net import Response
from fjordlens.website import (article_page, crawl_site, extract, feed, fetch_page,
                               sitemap_links, try_candidates, verification_links, url_key)

ORG, SITE = "923609016", "https://equinor.com/"
ENTITY = {"organisasjonsnummer": ORG, "navn": "EQUINOR ASA", "hjemmeside": SITE}
OWNER = "<title>Equinor ASA</title><footer>Equinor ASA Org nr 923609016</footer>"
DATE = "2026-10-17T00:00:00Z"


def response(text, path="", content_type="text/html", **kwargs):
    url = path if path.startswith("http") else SITE + path
    return Response(url, url, 200, text.encode(), {"content-type": content_type}, DATE, **kwargs)


class Fetcher:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def get(self, url, *args, **kwargs):
        self.calls.append(url)
        return self.responses.get(url, Response(url, url, 404, retrieved_at=DATE))


def proof():
    result = gate.assess(gate.Context(ENTITY), {"url": SITE, "origin": "registry_website"}, [parse_html(OWNER, SITE)])
    assert result["publishable"]
    return result


def publications(profile):
    return [c for c in profile.data["claims"] if c["field"] == "company_publication"]


class CrawlRecallTests(unittest.TestCase):
    def crawl(self, home, pages, max_pages=5):
        profile = Profile(ORG, "crawl-recall")
        home_response = response(OWNER + home)
        fetcher = Fetcher(pages)
        crawl_site(profile, fetcher, ORG, ENTITY, home_response, proof(),
                   [parse_html(home_response.text(), SITE)], [home_response], max_pages)
        return profile, fetcher

    def test_news_page_expands_to_root_slug_article_with_own_date(self):
        news = response('<h1>Nyheter</h1><a href="/new-agreement">New offshore agreement signed</a>', "nyheter")
        story = response('<meta property="og:type" content="article"><meta property="article:published_time" '
                         'content="2026-10-15"><h1>New offshore agreement signed</h1>', "new-agreement")
        profile, fetcher = self.crawl('<a href="/nyheter">Nyheter</a>', {news.url: news, story.url: story}, max_pages=3)
        self.assertEqual([c["value"]["url"] for c in publications(profile)], [story.url])
        self.assertEqual(len(fetcher.calls), 2)
        self.assertEqual(validate(profile.data), [])
        self.assertIn(publications(profile)[0]["claim_span"], story.text())

    def test_root_service_page_creation_date_is_not_activity(self):
        profile = Profile(ORG, "crawl-recall")
        page = response('<meta name="date" content="2026-10-15"><article><h1>Offshore services and support</h1></article>', "offshore-services")
        article_page(profile, parse_html(page.text(), page.url), page, proof())
        self.assertEqual(publications(profile), [])

    def test_cms_static_page_schema_does_not_create_a_news_item(self):
        for path in ("contact", "references", "services/design"):
            with self.subTest(path=path):
                profile = Profile(ORG, "crawl-recall")
                page = response('<body class="page page-template-default"><article class="page type-page"><h1>Company services</h1></article>'
                                '<meta property="og:type" content="article"><meta property="article:published_time" content="2020-01-01">'
                                '<script type="application/ld+json">{"@type":"Article","headline":"Company services",'
                                '"datePublished":"2020-01-01"}</script></body>', path)
                extract(profile, ENTITY, parse_html(page.text(), page.url), page, proof())
                self.assertEqual(publications(profile), [])

    def test_cms_post_template_preserves_real_article(self):
        profile = Profile(ORG, "crawl-recall")
        page = response('<body class="single single-post"><article class="post type-post"><h1>A new agreement</h1></article>'
                        '<script type="application/ld+json">{"@type":"Article","headline":"A new agreement",'
                        '"datePublished":"2026-10-10"}</script></body>', "new-agreement")
        extract(profile, ENTITY, parse_html(page.text(), page.url), page, proof())
        self.assertEqual(len(publications(profile)), 1)

    def test_explicit_news_or_blog_on_cms_page_remains_a_publication(self):
        for kind in ("NewsArticle", "BlogPosting", "PressRelease"):
            with self.subTest(kind=kind):
                profile = Profile(ORG, "crawl-recall")
                page = response('<body class="page page-template-default"><article class="page type-page"><h1>A new agreement</h1></article>'
                                '<script type="application/ld+json">{"@type":"' + kind + '","headline":"A new agreement",'
                                '"datePublished":"2026-10-10"}</script></body>', "new-agreement")
                extract(profile, ENTITY, parse_html(page.text(), page.url), page, proof())
                self.assertEqual(len(publications(profile)), 1)

    def test_jsonld_article_identity_keeps_functional_query(self):
        html = '<script type="application/ld+json">{"@type":"NewsArticle","url":"https://equinor.com/article?id=1",' \
               '"headline":"First company story","datePublished":"2026-10-12"}</script>'
        self.assertIsNone(article_published_date(html, DATE[:10], SITE + "article?id=2"))
        self.assertEqual(article_published_date(html, DATE[:10], SITE + "article?utm_source=mail&id=1")["published_on"], "2026-10-12")

    def test_visible_modification_dates_are_not_publication_dates(self):
        for timestamp in ('<time datetime="2026-10-12">Updated 12 October 2026</time>',
                          'Sist oppdatert <time datetime="2026-10-12">12. oktober 2026</time>',
                          '<time itemprop="dateModified" datetime="2026-10-12">12 October 2026</time>'):
            with self.subTest(timestamp=timestamp):
                html = '<article><h1>A company story</h1>' + timestamp + '</article>'
                self.assertIsNone(article_published_date(html, DATE[:10], SITE + "story"))

    def test_listing_updated_date_is_not_reused_as_publication(self):
        html = '<article><a href="/new-agreement">A new agreement</a><time datetime="2026-10-12">Updated 12 October 2026</time></article>'
        self.assertEqual(extract_dated_links(html, SITE + "news", DATE[:10]), [])

    def test_unicode_and_encoded_article_urls_dedupe_without_merging_encoded_slashes(self):
        self.assertEqual(url_key(SITE + "nyheter/økt-vekst/"), url_key(SITE + "nyheter/%C3%B8kt-vekst"))
        self.assertNotEqual(url_key(SITE + "article/a%2Fb"), url_key(SITE + "article/a/b"))
        self.assertNotEqual(url_key(SITE + "article?id=1"), url_key(SITE + "article?id=2"))

    def test_source_timezone_is_preserved_without_inventing_local_calendar_date(self):
        profile = Profile(ORG, "crawl-recall")
        page = response('<meta property="og:type" content="article"><h1>A company story</h1>'
                        '<meta property="article:published_time" content="2024-11-30T23:00:00Z">', "story")
        extract(profile, ENTITY, parse_html(page.text(), page.url), page, proof())
        self.assertEqual(publications(profile)[0]["value"]["published_on"], "2024-11-30")
        self.assertEqual(publications(profile)[0]["value"]["published_at"], "2024-11-30T23:00:00Z")

    def test_read_more_label_does_not_become_a_news_title(self):
        listing = '<article><h2>New offshore agreement signed</h2><time datetime="2026-10-15">15 Oct</time><a href="/new-agreement">Read more</a></article>'
        self.assertEqual(extract_dated_links(listing, SITE + "news", DATE[:10]), [])

    def test_known_site_suffix_is_removed_from_article_title(self):
        html = ('<meta property="og:title" content="New agreement signed | Equinor"><meta property="og:site_name" content="Equinor">'
                '<meta property="article:published_time" content="2026-10-15">')
        self.assertEqual(article_published_date(html, DATE[:10])["title"], "New agreement signed")

    def test_new_article_on_sibling_subdomain_is_not_crawled(self):
        other = "https://partner.equinor.com/news/agreement"
        news = response('<a href="' + other + '">New offshore agreement signed</a>', "nyheter")
        _, fetcher = self.crawl('<a href="/nyheter">Nyheter</a>', {news.url: news})
        self.assertNotIn(other, fetcher.calls)

    def test_dynamic_frontier_respects_page_limit(self):
        news = response(''.join(f'<a href="/news/article-{i}">New offshore agreement number {i}</a>' for i in range(30)), "news")
        _, fetcher = self.crawl('<a href="/news">News</a>', {news.url: news}, max_pages=4)
        self.assertEqual(len(fetcher.calls), 3)  # Home is the fourth page.

    def test_contact_menu_does_not_suppress_sitemap_discovery(self):
        sm = response('<urlset><url><loc>' + SITE + 'new-agreement</loc></url></urlset>', "sitemap.xml", "application/xml")
        _, fetcher = self.crawl('<a href="/kontakt">Kontakt</a><a href="/about">About</a><a href="/privacy">Privacy</a>', {sm.url: sm})
        self.assertIn(sm.url, fetcher.calls)


class SitemapTests(unittest.TestCase):
    def test_nested_post_sitemap_prefers_recent_articles_and_never_uses_lastmod_as_publication(self):
        root = response('<sitemapindex><sitemap><loc>' + SITE + 'page-sitemap.xml</loc></sitemap>'
                        '<sitemap><loc>' + SITE + 'post-sitemap.xml</loc></sitemap></sitemapindex>', "sitemap.xml", "application/xml")
        posts = response('<urlset><url><loc>' + SITE + 'old-agreement</loc><lastmod>2023-01-01</lastmod></url>'
                         '<url><loc>' + SITE + 'new-agreement</loc><lastmod>2026-10-16</lastmod></url>'
                         '<url><loc>https://sister.equinor.com/article</loc></url></urlset>', "post-sitemap.xml", "application/xml")
        fetcher = Fetcher({root.url: root, posts.url: posts})
        profile = Profile(ORG, "crawl-recall")
        links = sitemap_links(profile, fetcher, ORG, SITE, max_documents=2)
        self.assertEqual([l["url"] for l in links], [SITE + "new-agreement", SITE + "old-agreement"])
        self.assertEqual(fetcher.calls, [root.url, posts.url])
        self.assertEqual(publications(profile), [])

    def test_cyclic_indexes_and_external_indexes_are_bounded(self):
        sm = response('<sitemapindex><sitemap><loc>' + SITE + 'sitemap.xml</loc></sitemap>'
                      '<sitemap><loc>https://other.no/posts.xml</loc></sitemap></sitemapindex>', "sitemap.xml", "application/xml")
        fetcher = Fetcher({sm.url: sm})
        self.assertEqual(sitemap_links(Profile(ORG, "crawl"), fetcher, ORG, SITE), [])
        self.assertEqual(fetcher.calls, [sm.url])

    def test_xml_entities_are_never_parsed(self):
        sm = response('<!DOCTYPE urlset [<!ENTITY data SYSTEM "file:///etc/passwd">]><urlset>&data;</urlset>',
                      "sitemap.xml", "application/xml")
        self.assertEqual(sitemap_links(Profile(ORG, "crawl"), Fetcher({sm.url: sm}), ORG, SITE), [])


class FeedTests(unittest.TestCase):
    def test_catalog_feed_is_not_company_publication(self):
        xml = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Tea product 500g</title>'
               '<link href="/new-item"/><published>2026-10-09T11:18:18+02:00</published></entry></feed>')
        for path in ("collections/all.atom", "products.atom", "?feed=rss2&post_type=product"):
            with self.subTest(path=path):
                profile = Profile(ORG, "catalog-test")
                feed(profile, ENTITY, response(xml, path, "application/atom+xml"), proof())
                self.assertEqual(publications(profile), [])

    def test_mixed_feed_keeps_genuine_blog_entry_and_skips_product(self):
        xml = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Tea product 500g</title>'
               '<link href="/products/tea-500g"/><published>2026-10-09</published></entry>'
               '<entry><title>A guide to products from Norway</title><link href="/blogs/news/products-from-norway"/>'
               '<published>2026-10-08</published></entry></feed>')
        profile = Profile(ORG, "catalog-test")
        feed(profile, ENTITY, response(xml, "blog.atom", "application/atom+xml"), proof())
        self.assertEqual([c["value"]["url"] for c in publications(profile)], [SITE + "blogs/news/products-from-norway"])

    def test_shopify_product_metadata_is_rejected_even_on_unusual_urls(self):
        xml = ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:s="http://jadedpixel.com/-/spec/shopify">'
               '<entry><title>Tea product 500g</title><link href="/tea-500g"/><published>2026-10-09</published>'
               '<s:vendor>Tea producer</s:vendor></entry></feed>')
        profile = Profile(ORG, "catalog-test")
        feed(profile, ENTITY, response(xml, "feed", "application/atom+xml"), proof())
        self.assertEqual(publications(profile), [])

    def test_product_page_generic_article_metadata_is_not_news(self):
        page = response('<h1>Tea product 500g</h1><meta property="og:type" content="article">'
                        '<meta property="article:published_time" content="2026-10-09">'
                        '<script type="application/ld+json">{"@type":"Article","headline":"Tea product 500g",'
                        '"datePublished":"2026-10-09"}</script>', "products/tea-500g")
        profile = Profile(ORG, "catalog-test")
        extract(profile, ENTITY, parse_html(page.text(), page.url), page, proof())
        self.assertEqual(publications(profile), [])

    def test_wordpress_comment_feed_is_not_company_activity(self):
        xml = ('<rss><channel><title>Kommentarer til NOVOOI</title><item>'
               '<title>Kommentar til BRUTALIST mug, Naaz Ceramics av Novooi</title>'
               '<link>' + SITE + 'produkt/brutalist-mug-naaz-ceramics/#comment-668</link>'
               '<pubDate>Fri, 28 Nov 2025 13:28:37 +0000</pubDate>'
               '<description>Amazing craft and wonderful designer!</description></item></channel></rss>')
        for path in ("comments/feed/", "feed?feed=comments-rss2", "syndication"):
            with self.subTest(path=path):
                profile = Profile(ORG, "crawl-recall")
                feed(profile, ENTITY, response(xml, path, "application/rss+xml"), proof())
                self.assertEqual(publications(profile), [])

    def test_comment_anchor_in_an_ordinary_feed_is_not_company_activity(self):
        xml = ('<rss><channel><title>Company news</title><item><title>Amazing company product</title><link>'
               + SITE + 'product/#comment-668</link><pubDate>Fri, 28 Nov 2025 13:28:37 +0000</pubDate></item></channel></rss>')
        profile = Profile(ORG, "crawl-recall")
        feed(profile, ENTITY, response(xml, "feed", "application/rss+xml"), proof())
        self.assertEqual(publications(profile), [])

    def test_atom_uses_alternate_link_and_publication_date(self):
        xml = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>New agreement announced</title>'
               '<link rel="self" href="/api/post-1"/><link rel="alternate" href="/new-agreement"/>'
               '<published>2026-10-10</published><updated>2026-10-15</updated></entry></feed>')
        profile = Profile(ORG, "crawl-recall")
        feed(profile, ENTITY, response(xml, "feed", "application/atom+xml"), proof())
        self.assertEqual([(c["value"]["url"], c["value"]["published_on"]) for c in publications(profile)],
                         [(SITE + "new-agreement", "2026-10-10")])
        self.assertIn(publications(profile)[0]["claim_span"], xml)

    def test_updated_only_is_not_a_publication_date(self):
        xml = '<feed><entry><title>New agreement announced</title><link href="/new-agreement"/><updated>2026-10-15</updated></entry></feed>'
        profile = Profile(ORG, "crawl-recall")
        feed(profile, ENTITY, response(xml, "feed", "application/atom+xml"), proof())
        self.assertEqual(publications(profile), [])

    def test_rss_deduplicates_existing_article_and_cleans_title_markup(self):
        profile = Profile(ORG, "crawl-recall")
        page = response('<meta property="og:type" content="article"><meta property="article:published_time" content="2026-10-15">'
                        '<h1>New offshore agreement signed</h1>', "new-agreement/")
        extract(profile, ENTITY, parse_html(page.text(), page.url), page, proof())
        xml = ('<rss><channel><item><title>New offshore agreement signed</title><link>' + SITE +
               'new-agreement?utm_source=feed</link><pubDate>Thu, 15 Oct 2026 10:00:00 GMT</pubDate></item>'
               '<item><title><![CDATA[An &amp; another <em>agreement</em>]]></title><link>' + SITE +
               'another-agreement</link><pubDate>Thu, 15 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>')
        feed(profile, ENTITY, response(xml, "feed", "application/rss+xml"), proof())
        self.assertEqual(len(publications(profile)), 2)
        self.assertEqual(publications(profile)[1]["value"]["title"], "An & another agreement")
        self.assertEqual(publications(profile)[1]["value"]["published_at"], "Thu, 15 Oct 2026 10:00:00 GMT")
        self.assertEqual(profile.data["errors"], [])


class VerificationTests(unittest.TestCase):
    def test_legal_page_is_not_hidden_behind_multiple_contact_languages(self):
        page = parse_html('<a href="/contact">Contact</a><a href="/kontakt">Kontakt</a><a href="/privacy">Privacy</a>', SITE)
        self.assertEqual([l["url"] for l in verification_links(page, SITE, limit=2)], [SITE + "contact", SITE + "privacy"])

    def test_search_result_deep_page_can_prove_the_candidate(self):
        home = response('<html lang="nb"><title>Equinor ASA</title><p>We provide energy and offshore services for customers across Norway.</p></html>')
        legal = response(OWNER, "company-information")
        fetcher = Fetcher({SITE: home, legal.url: legal})
        accepted, _ = try_candidates(Profile(ORG, "crawl"), fetcher, ORG, gate.Context(ENTITY),
                                    [{"url": SITE, "origin": "search_orgnr", "verification_url": legal.url}], "not_available")
        self.assertIsNotNone(accepted)
        self.assertEqual(fetcher.calls, [SITE, legal.url])

    def test_transport_failure_tries_www_over_https(self):
        failed = Response(SITE, SITE, error="Unreachable: robots.txt could not be fetched (Connection reset by peer)")
        working = response(OWNER, "https://www.equinor.com/")
        fetcher = Fetcher({SITE: failed, working.url: working})
        found = fetch_page(Profile(ORG, "crawl"), fetcher, ORG, SITE)
        self.assertEqual(found.url, working.url)
        self.assertEqual(fetcher.calls, [SITE, working.url])

    def test_robots_disallow_and_http_denial_do_not_get_fallbacks(self):
        for denied in (Response(SITE, SITE, error="Robots: access is disallowed by robots.txt"), Response(SITE, SITE, 403)):
            with self.subTest(denied=denied):
                fetcher = Fetcher({SITE: denied})
                fetch_page(Profile(ORG, "crawl"), fetcher, ORG, SITE)
                self.assertEqual(fetcher.calls, [SITE])


class DomainNominationTests(unittest.TestCase):
    def test_bounded_trade_descriptor_and_as_suffix_candidates(self):
        self.assertIn("skya.no", discovery.guess_domains("SKYA MARKEDSFØRING AS"))
        candidates = discovery.guess_domains("BAUGE AS")
        self.assertIn("baugeas.no", candidates)
        self.assertIn("bauge-as.no", candidates)
        self.assertLessEqual(len(discovery.guess_domains("SKYA MARKEDSFØRING AS")), 6)


if __name__ == "__main__":
    unittest.main()
