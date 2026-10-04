import unittest
from fjordlens.articles import extract_dated_links, article_published_date


class TestExtractDatedLinks(unittest.TestCase):
    def test_basic_time_element_in_li(self):
        html = '<li><a href="/nyheter/ny-avtale">Ny avtale med Statens vegvesen</a> <time datetime="2025-03-14">14. mars 2025</time></li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Ny avtale med Statens vegvesen')
        self.assertEqual(results[0]['url'], 'https://www.firma.no/nyheter/ny-avtale')
        self.assertEqual(results[0]['published_on'], '2025-03-14')
        self.assertEqual(results[0]['date_source'], 'time_element')
        self.assertIn('<time datetime="2025-03-14">', results[0]['span'])
        self.assertIn(results[0]['span'], html)

    def test_norwegian_month_full_name(self):
        html = '<li><a href="/artikkel/1">Test artikkel</a> 12. desember 2023</li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['published_on'], '2023-12-12')
        self.assertEqual(results[0]['date_source'], 'block_text')
        self.assertIn('12. desember 2023', results[0]['span'])
        self.assertIn(results[0]['span'], html)

    def test_norwegian_month_abbreviation_with_dot(self):
        html = '<li><a href="/artikkel/2">Test artikkel to</a> 3. okt. 2024</li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['published_on'], '2024-10-03')
        self.assertEqual(results[0]['date_source'], 'block_text')
        self.assertIn('3. okt. 2024', results[0]['span'])
        self.assertIn(results[0]['span'], html)

    def test_norwegian_month_abbreviation_no_dot(self):
        html = '<li><a href="/artikkel/3">Test artikkel tre</a> 14. mars 2025</li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['published_on'], '2025-03-14')
        self.assertIn('14. mars 2025', results[0]['span'])
        self.assertIn(results[0]['span'], html)

    def test_numeric_date_dot_separator(self):
        html = '<li><a href="/artikkel/4">Test artikkel fire</a> 14.03.2025</li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['published_on'], '2025-03-14')
        self.assertIn('14.03.2025', results[0]['span'])
        self.assertIn(results[0]['span'], html)

    def test_numeric_date_slash_separator(self):
        html = '<li><a href="/artikkel/5">Test artikkel fem</a> 4/3/2025</li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['published_on'], '2025-03-04')
        self.assertIn('4/3/2025', results[0]['span'])
        self.assertIn(results[0]['span'], html)

    def test_english_month_name_full(self):
        html = '<li><a href="/artikkel/6">Test artikkel seks</a> March 14, 2025</li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['published_on'], '2025-03-14')
        self.assertIn('March 14, 2025', results[0]['span'])
        self.assertIn(results[0]['span'], html)

    def test_english_day_month_year(self):
        html = '<li><a href="/artikkel/7">Test artikkel syv</a> 14 March 2025</li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['published_on'], '2025-03-14')
        self.assertIn('14 March 2025', results[0]['span'])
        self.assertIn(results[0]['span'], html)

    def test_url_path_full_date(self):
        html = '<li><a href="/2025/03/14/artikkel">Test artikkel url</a></li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['published_on'], '2025-03-14')
        self.assertEqual(results[0]['date_source'], 'url_path')
        self.assertIn('/2025/03/14/', results[0]['span'])
        self.assertIn(results[0]['span'], html)

    def test_url_path_only_year_month_rejected(self):
        html = '<li><a href="/2025/03/artikkel">Test artikkel url</a></li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 0)

    def test_future_date_excluded(self):
        html = '<li><a href="/artikkel/future">Future article</a> <time datetime="2025-07-01">1. juli 2025</time></li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 0)

    def test_old_date_before_1990_excluded(self):
        html = '<li><a href="/artikkel/old">Old article</a> <time datetime="1985-05-15">15. mai 1985</time></li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 0)

    def test_invalid_date_excluded(self):
        html = '<li><a href="/artikkel/invalid">Invalid date</a> 31.02.2025</li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 0)

    def test_other_domain_excluded(self):
        html = '<li><a href="https://other.no/artikkel">Other domain</a> <time datetime="2025-03-14">14. mars 2025</time></li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 0)

    def test_other_subdomain_is_another_host(self):
        # A subdomain can be a sister company's site (eiendom.bergenbil.no): only the verified host counts.
        html = '<li><a href="https://blogg.firma.no/artikkel">Blogg artikkel</a> <time datetime="2025-03-14">14. mars 2025</time></li>'
        self.assertEqual(extract_dated_links(html, 'https://www.firma.no/nyheter', '2025-06-01'), [])

    def test_www_and_bare_host_are_the_same_site(self):
        html = '<li><a href="https://firma.no/artikkel">Artikkel uten www</a> <time datetime="2025-03-14">14. mars 2025</time></li>'
        results = extract_dated_links(html, 'https://www.firma.no/nyheter', '2025-06-01')
        self.assertEqual([r['url'] for r in results], ['https://firma.no/artikkel'])

    def test_duplicate_url_kept_once(self):
        html = '''
        <li><a href="/artikkel/samme">Første artikkel</a> <time datetime="2025-03-14">14. mars 2025</time></li>
        <li><a href="/artikkel/samme">Andre artikkel</a> <time datetime="2025-03-15">15. mars 2025</time></li>
        '''
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Første artikkel')

    def test_title_too_short_skipped(self):
        html = '<li><a href="/artikkel/kort">Cort</a> <time datetime="2025-03-14">14. mars 2025</time></li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 0)

    def test_title_only_date_skipped(self):
        html = '<li><a href="/artikkel/dato">14.03.2025</a> <time datetime="2025-03-14">14. mars 2025</time></li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 0)

    def test_relative_href_resolved(self):
        html = '<li><a href="nyheter/artikkel">Relativ lenke</a> <time datetime="2025-03-14">14. mars 2025</time></li>'
        page_url = 'https://www.firma.no/'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['url'], 'https://www.firma.no/nyheter/artikkel')

    def test_fragment_removed(self):
        html = '<li><a href="/artikkel#section">Artikkel med fragment</a> <time datetime="2025-03-14">14. mars 2025</time></li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertNotIn('#', results[0]['url'])
        self.assertEqual(results[0]['url'], 'https://www.firma.no/artikkel')

    def test_sorted_newest_first(self):
        html = '''
        <li><a href="/artikkel/eldre">Eldre artikkel</a> <time datetime="2025-01-01">1. januar 2025</time></li>
        <li><a href="/artikkel/nyere">Nyere artikkel</a> <time datetime="2025-03-14">14. mars 2025</time></li>
        <li><a href="/artikkel/mellom">Mellom artikkel</a> <time datetime="2025-02-15">15. februar 2025</time></li>
        '''
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]['published_on'], '2025-03-14')
        self.assertEqual(results[1]['published_on'], '2025-02-15')
        self.assertEqual(results[2]['published_on'], '2025-01-01')

    def test_max_10_items(self):
        html = ''
        for i in range(15):
            html += f'<li><a href="/artikkel/{i}">Artikkel nummer {i} med lang tittel</a> <time datetime="2025-03-{14+i:02d}">14. mars 2025</time></li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 10)

    def test_mailto_tel_javascript_skipped(self):
        html = '''
        <li><a href="mailto:test@example.com">Email</a> <time datetime="2025-03-14">14. mars 2025</time></li>
        <li><a href="tel:+123456789">Phone</a> <time datetime="2025-03-14">14. mars 2025</time></li>
        <li><a href="javascript:void(0)">JS</a> <time datetime="2025-03-14">14. mars 2025</time></li>
        '''
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 0)

    def test_file_links_skipped(self):
        html = '''
        <li><a href="/file.pdf">PDF</a> <time datetime="2025-03-14">14. mars 2025</time></li>
        <li><a href="/image.jpg">JPG</a> <time datetime="2025-03-14">14. mars 2025</time></li>
        <li><a href="/doc.docx">DOCX</a> <time datetime="2025-03-14">14. mars 2025</time></li>
        '''
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 0)

    def test_time_element_inside_article(self):
        html = '<article><a href="/artikkel">Artikkel i article</a> <time datetime="2025-03-14">14. mars 2025</time></article>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['date_source'], 'time_element')

    def test_time_element_inside_div(self):
        html = '<div><a href="/artikkel">Artikkel i div</a> <time datetime="2025-03-14">14. mars 2025</time></div>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['date_source'], 'time_element')

    def test_iso_date_in_block_text(self):
        html = '<li><a href="/artikkel">Artikkel med ISO dato</a> Publisert: 2025-03-14</li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['published_on'], '2025-03-14')
        self.assertEqual(results[0]['date_source'], 'block_text')
        self.assertIn('2025-03-14', results[0]['span'])
        self.assertIn(results[0]['span'], html)

    def test_english_month_abbreviation(self):
        html = '<li><a href="/artikkel">Artikkel med engelsk forkortelse</a> 14 Mar 2025</li>'
        page_url = 'https://www.firma.no/nyheter'
        today = '2025-06-01'
        results = extract_dated_links(html, page_url, today)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['published_on'], '2025-03-14')
        self.assertIn('14 Mar 2025', results[0]['span'])
        self.assertIn(results[0]['span'], html)


class TestArticlePublishedDate(unittest.TestCase):
    def test_meta_article_published_time(self):
        html = '''
        <html>
        <head>
            <meta property="article:published_time" content="2025-03-14T09:30:00+02:00">
            <meta property="og:title" content="Test Article Title">
        </head>
        <body><article><h1>Article H1</h1></article></body>
        </html>
        '''
        result = article_published_date(html)
        self.assertIsNotNone(result)
        self.assertEqual(result['published_on'], '2025-03-14')
        self.assertEqual(result['source'], 'meta')
        self.assertEqual(result['title'], 'Test Article Title')
        self.assertIn('2025-03-14T09:30:00+02:00', result['span'])
        self.assertIn(result['span'], html)

    def test_meta_name_date(self):
        html = '''
        <html>
        <head>
            <meta name="date" content="2025-03-14">
            <title>Page Title</title>
        </head>
        <body></body>
        </html>
        '''
        result = article_published_date(html)
        self.assertIsNotNone(result)
        self.assertEqual(result['published_on'], '2025-03-14')
        self.assertEqual(result['source'], 'meta')
        self.assertEqual(result['title'], 'Page Title')
        self.assertIn('2025-03-14', result['span'])
        self.assertIn(result['span'], html)

    def test_jsonld_in_graph(self):
        html = '''
        <html>
        <head>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@graph": [
                    {"@type": "Article", "datePublished": "2025-03-14T10:00:00Z", "headline": "JSON-LD Article"}
                ]
            }
            </script>
        </head>
        <body></body>
        </html>
        '''
        result = article_published_date(html)
        self.assertIsNotNone(result)
        self.assertEqual(result['published_on'], '2025-03-14')
        self.assertEqual(result['source'], 'jsonld')
        self.assertEqual(result['title'], 'JSON-LD Article')
        self.assertIn('datePublished', result['span'])
        self.assertIn(result['span'], html)

    def test_jsonld_top_level_list(self):
        html = '''
        <html>
        <head>
            <script type="application/ld+json">
            [{"@type": "NewsArticle", "datePublished": "2025-03-14", "headline": "List Article"}]
            </script>
        </head>
        <body></body>
        </html>
        '''
        result = article_published_date(html)
        self.assertIsNotNone(result)
        self.assertEqual(result['published_on'], '2025-03-14')
        self.assertEqual(result['source'], 'jsonld')
        self.assertEqual(result['title'], 'List Article')

    def test_time_element_in_article(self):
        html = '''
        <html>
        <head><title>Time Article</title></head>
        <body>
            <article>
                <h1>Article H1</h1>
                <time datetime="2025-03-14">14. mars 2025</time>
            </article>
        </body>
        </html>
        '''
        result = article_published_date(html)
        self.assertIsNotNone(result)
        self.assertEqual(result['published_on'], '2025-03-14')
        self.assertEqual(result['source'], 'time_element')
        self.assertEqual(result['title'], 'Article H1')
        self.assertIn('<time datetime="2025-03-14">', result['span'])
        self.assertIn(result['span'], html)

    def test_none_when_no_date(self):
        html = '<html><head><title>No Date</title></head><body><article><h1>No Date Article</h1></article></body></html>'
        result = article_published_date(html)
        self.assertIsNone(result)

    def test_title_fallback_og_title(self):
        html = '''
        <html>
        <head>
            <meta property="article:published_time" content="2025-03-14">
            <meta property="og:title" content="OG Title">
        </head>
        <body><article><h1>H1 Title</h1></article></body>
        </html>
        '''
        result = article_published_date(html)
        self.assertIsNotNone(result)
        self.assertEqual(result['title'], 'OG Title')

    def test_title_fallback_h1(self):
        html = '''
        <html>
        <head>
            <meta property="article:published_time" content="2025-03-14">
        </head>
        <body><article><h1>H1 Title</h1></article></body>
        </html>
        '''
        result = article_published_date(html)
        self.assertIsNotNone(result)
        self.assertEqual(result['title'], 'H1 Title')

    def test_title_fallback_title_tag(self):
        html = '''
        <html>
        <head>
            <meta property="article:published_time" content="2025-03-14">
            <title>Title Tag</title>
        </head>
        <body><article></article></body>
        </html>
        '''
        result = article_published_date(html)
        self.assertIsNotNone(result)
        self.assertEqual(result['title'], 'Title Tag')

    def test_date_with_time_part_stripped(self):
        html = '''
        <html>
        <head>
            <meta property="article:published_time" content="2025-03-14T09:30:00+02:00">
        </head>
        <body></body>
        </html>
        '''
        result = article_published_date(html)
        self.assertIsNotNone(result)
        self.assertEqual(result['published_on'], '2025-03-14')


class ListingQuoteAndPairingTests(unittest.TestCase):
    PAGE = 'https://www.firma.no/nyheter'

    def test_quote_covers_link_and_date_verbatim(self):
        html = '<ul><li><a href="/nyheter/ny-avtale">Ny avtale med Statens vegvesen</a> <time datetime="2025-03-14">14. mars 2025</time></li></ul>'
        item = extract_dated_links(html, self.PAGE, '2025-06-01')[0]
        self.assertEqual(item['span'], '<a href="/nyheter/ny-avtale">Ny avtale med Statens vegvesen</a> <time datetime="2025-03-14">14. mars 2025</time>')

    def test_flat_list_dates_are_not_paired_by_container(self):
        html = ('<div class="news"><a href="/n/forste">Første nyhet i lista</a> <time datetime="2025-03-14">14. mars</time>'
                '<a href="/n/andre">Andre nyhet i lista</a> <time datetime="2025-02-01">1. feb</time></div>')
        self.assertEqual(extract_dated_links(html, self.PAGE, '2025-06-01'), [])

    def test_flat_list_dates_inside_links_are_used(self):
        html = ('<div class="news"><a href="/n/forste">Første nyhet i lista <time datetime="2025-03-14">14.03.2025</time></a>'
                '<a href="/n/andre">Andre nyhet i lista <time datetime="2025-02-01">01.02.2025</time></a></div>')
        results = {r['url']: r for r in extract_dated_links(html, self.PAGE, '2025-06-01')}
        self.assertEqual(results['https://www.firma.no/n/forste']['published_on'], '2025-03-14')
        self.assertEqual(results['https://www.firma.no/n/andre']['published_on'], '2025-02-01')
        self.assertEqual(results['https://www.firma.no/n/forste']['title'], 'Første nyhet i lista')


    def test_headline_and_time_share_a_heading_inside_a_block_with_other_links(self):
        html = ('<article><div class="textwrap"><header><h2><a href="/generell-informasjon">Generell informasjon inntak</a>'
                '<time datetime="2019-02-18">18. februar 2019</time></h2></header><div class="ingress"><p>Se '
                '<a href="/orientering-til-sokere">Orientering til søkere</a></p></div></div></article>')
        results = extract_dated_links(html, self.PAGE, '2025-06-01')
        self.assertEqual([(r['url'], r['published_on']) for r in results], [('https://www.firma.no/generell-informasjon', '2019-02-18')])
        self.assertIn('href="/generell-informasjon"', results[0]['span'])

    def test_script_text_dates_are_ignored(self):
        html = '<li><a href="/n/sak">Nyhet uten dato</a><script>var d = "2025-03-14";</script></li>'
        self.assertEqual(extract_dated_links(html, self.PAGE, '2025-06-01'), [])


    def test_public_suffix_domains_do_not_collide(self):
        html = '<li><a href="https://unrelated.co.uk/news/sale">Unrelated sale news</a> <time datetime="2025-03-14">14 March 2025</time></li>'
        self.assertEqual(extract_dated_links(html, 'https://company.co.uk/news', '2025-06-01'), [])

    def test_short_titled_competing_link_blocks_pairing(self):
        html = ('<div><div><a href="/a">The first article</a></div><div><a href="/b">News</a>'
                '<time datetime="2025-03-14">14 March 2025</time></div></div>')
        self.assertEqual(extract_dated_links(html, self.PAGE, '2025-06-01'), [])

    def test_link_and_date_too_far_apart_to_quote_is_skipped(self):
        html = ('<li><a href="/n/sak">Sak med lang avstand</a>' + '<span>' + 'x' * 2500 + '</span>'
                '<time datetime="2025-03-14">14. mars 2025</time></li>')
        self.assertEqual(extract_dated_links(html, self.PAGE, '2025-06-01'), [])

    def test_deep_nesting_is_bounded(self):
        import time
        html = '<div>x' * 20000 + '<a href="/n/sak">Dyp nyhet her</a><time datetime="2025-03-14"></time>' + '</div>' * 20000
        started = time.monotonic()
        extract_dated_links(html, self.PAGE, '2025-06-01')
        self.assertLess(time.monotonic() - started, 20)

    def test_card_with_read_more_link_to_same_article(self):
        html = ('<article><h3><a href="/n/sak">Ny sak om utbygging</a></h3><time datetime="2025-04-02">2. april 2025</time>'
                '<p>Ingress.</p><a href="/n/sak">Les mer om saken</a></article>')
        results = extract_dated_links(html, self.PAGE, '2025-06-01')
        self.assertEqual([(r['url'], r['published_on']) for r in results], [('https://www.firma.no/n/sak', '2025-04-02')])


if __name__ == '__main__':
    unittest.main()

class ArticlePageBindingTests(unittest.TestCase):
    def test_jsonld_article_about_another_page_is_not_this_pages_date(self):
        html = ('<h1>New contract announced</h1><script type="application/ld+json">'
                '{"@type": "Article", "url": "https://other.no/old", "datePublished": "2021-01-01"}</script>')
        self.assertIsNone(article_published_date(html, '2025-06-01', page_url='https://firma.no/news/new'))

    def test_jsonld_article_for_this_page_is_used(self):
        html = ('<h1>New contract announced</h1><script type="application/ld+json">'
                '{"@type": "NewsArticle", "mainEntityOfPage": {"@id": "https://www.firma.no/news/new/"}, "datePublished": "2025-03-01"}</script>')
        found = article_published_date(html, '2025-06-01', page_url='https://firma.no/news/new')
        self.assertEqual(found['published_on'], '2025-03-01')

    def test_listing_with_several_article_elements_has_no_page_date(self):
        html = ('<article><a href="/n/a">A</a><time datetime="2025-03-01">1</time></article>'
                '<article><a href="/n/b">B</a><time datetime="2025-02-01">2</time></article>')
        self.assertIsNone(article_published_date(html, '2025-06-01', page_url='https://firma.no/news/page/2'))

    def test_malformed_jsonld_types_do_not_raise(self):
        html = ('<script type="application/ld+json">{"@type": {"name": "Article"}, "datePublished": "2025-03-14"}</script>'
                '<script type="application/ld+json">{"@type": [["Article"]], "datePublished": "2025-03-14"}</script>')
        self.assertIsNone(article_published_date(html, '2025-06-01', page_url='https://firma.no/news/x'))
