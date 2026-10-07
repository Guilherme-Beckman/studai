package services

import (
	"fmt"
	"image/jpeg"
	"os"
	"path/filepath"

	"github.com/gen2brain/go-fitz"
)

func ExtractPagesAsImages(docPath string) {
	doc, err := fitz.New(docPath)

	if err != nil {
		panic(err)
	}

	defer doc.Close()

	tmpDir, err := os.MkdirTemp(os.TempDir(), "fitz")

	if err != nil {
		panic(err)
	}

	for n := 0; n < doc.NumPage(); n++ {
		img, err := doc.Image(n)

		if err != nil {
			panic(err)
		}
		f, err := os.Create(filepath.Join(tmpDir, fmt.Sprintf("Page_%03d.jpg", n)))

		if err != nil {
			panic(err)
		}

		err = jpeg.Encode(f, img, &jpeg.Options{jpeg.DefaultQuality})

		if err != nil {
			panic(err)
		}

		f.Close()

	}

}
func ExtractPagesAsSVG(docPath string) map[int]string {
	doc, err := fitz.New(docPath)

	if err != nil {
		panic(err)
	}

	defer doc.Close()

	if err != nil {
		panic(err)
	}
	r := make(map[int]string)

	//pula primeira pagina
	for n := 1; n < doc.NumPage(); n++ {
		//func (f *Document) SVG(pageNumber int) (string, error)

		svg, err := doc.SVG(n)

		if err != nil {
			panic(err)
		}
		r[n+1] = svg
	}
	return r
}
